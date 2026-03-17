"""Claude Code CLI adapter.

Spawns: claude -p "<prompt>" --output-format stream-json --verbose
Reads: JSONL with types "stream_event", "assistant", "result"

Uses -p (print) flag for one-shot queries. The --input-format stream-json mode
requires a control protocol initialization handshake that adds complexity;
-p keeps it simple while still getting full streaming output.
"""

from __future__ import annotations

import json
import shutil
from typing import AsyncGenerator

from ..base import BaseAdapter
from ..events import (
    BridgeEvent,
    SessionInit,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnComplete,
)
from ..subprocess_runner import SubprocessRunner
from ..types import ProviderType, SessionConfig, resolve_permission

PROVIDER = ProviderType.CLAUDE


class ClaudeAdapter(BaseAdapter):
    provider = PROVIDER

    def build_command(self, config: SessionConfig) -> list[str]:
        cli = shutil.which("claude")
        if not cli:
            raise FileNotFoundError(
                "claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
            )

        # Prepend prior context for cross-provider transfer
        prompt = config.prompt
        if config.prior_context:
            prompt = config.prior_context + "\n\n" + prompt

        cmd = [
            cli,
            "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
        ]

        # System prompt
        if config.system_prompt is not None:
            cmd.extend(["--system-prompt", config.system_prompt])

        if config.model:
            cmd.extend(["--model", config.model])

        pm = resolve_permission(config) or "bypassPermissions"
        cmd.extend(["--permission-mode", pm])

        # Session resume / fork
        if config.resume_session_id:
            cmd.extend(["--resume", config.resume_session_id])
        if config.fork_session:
            cmd.append("--fork-session")

        # Include partial messages for streaming tool args
        cmd.append("--include-partial-messages")

        # Memory control: skip user-level memory, only use project config (CLAUDE.md)
        if config.disable_global_memory:
            cmd.extend(["--setting-sources", "project,local"])

        cmd.extend(config.extra_args)
        return cmd

    def build_env(self, config: SessionConfig) -> dict[str, str]:
        env: dict[str, str] = {}
        # Unset CLAUDECODE to allow spawning Claude CLI from within a Claude session
        env["CLAUDECODE"] = ""
        env.update(config.env)
        return env

    async def stream(
        self,
        runner: SubprocessRunner,
        config: SessionConfig,
    ) -> AsyncGenerator[BridgeEvent, None]:
        # Prompt is passed via -p flag, so just close stdin
        await runner.close_stdin()

        pending_tool: dict | None = None
        session_id_yielded = False

        async for data in runner.read_events():
            msg_type = data.get("type", "")

            # ── Stream events (token-level) ────────────────────
            if msg_type == "stream_event":
                sid = data.get("session_id", "")
                if not session_id_yielded and sid:
                    session_id_yielded = True
                    yield SessionInit(session_id=sid, provider=PROVIDER.value, raw=data)

                evt = data.get("event", {})
                evt_type = evt.get("type", "")

                if evt_type == "content_block_delta":
                    delta = evt.get("delta", {})
                    dt = delta.get("type", "")
                    if dt == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield TextDelta(text=text, provider=PROVIDER.value, raw=data)
                    elif dt == "input_json_delta" and pending_tool:
                        pending_tool["input_json"] += delta.get("partial_json", "")

                elif evt_type == "content_block_start":
                    block = evt.get("content_block", {})
                    if block.get("type") == "tool_use":
                        pending_tool = {
                            "id": block["id"],
                            "name": block["name"],
                            "input_json": "",
                        }
                        yield ToolStart(
                            tool_call_id=block["id"],
                            name=block["name"],
                            provider=PROVIDER.value,
                            raw=data,
                        )

                elif evt_type == "content_block_stop":
                    if pending_tool:
                        try:
                            args = (
                                json.loads(pending_tool["input_json"])
                                if pending_tool["input_json"]
                                else {}
                            )
                        except json.JSONDecodeError:
                            args = {}
                        yield ToolStart(
                            tool_call_id=pending_tool["id"],
                            name=pending_tool["name"],
                            arguments=args,
                            provider=PROVIDER.value,
                            raw=data,
                        )
                        pending_tool = None

            # ── Messages containing tool results (user or assistant) ──
            elif msg_type in ("assistant", "user"):
                message = data.get("message", {})
                content = message.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if block.get("type") == "tool_result":
                            result_content = block.get("content", "")
                            if isinstance(result_content, list):
                                result_content = str(result_content)
                            yield ToolEnd(
                                tool_call_id=block.get("tool_use_id", ""),
                                name="",
                                result=result_content if isinstance(result_content, str) else str(result_content),
                                is_error=bool(block.get("is_error")),
                                provider=PROVIDER.value,
                                raw=data,
                            )

            # ── Result (turn complete) ─────────────────────────
            elif msg_type == "result":
                sid = data.get("session_id", "")
                if not session_id_yielded and sid:
                    yield SessionInit(session_id=sid, provider=PROVIDER.value, raw=data)

                # Extract token usage from result
                token_usage = None
                usage = data.get("usage", {})
                if usage:
                    token_usage = {
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                        "cached_input_tokens": usage.get("cache_read_input_tokens", 0),
                    }

                yield TurnComplete(
                    session_id=sid,
                    is_error=data.get("is_error", False),
                    duration_ms=data.get("duration_ms"),
                    cost_usd=data.get("total_cost_usd"),
                    num_turns=data.get("num_turns"),
                    token_usage=token_usage,
                    provider=PROVIDER.value,
                    raw=data,
                )

    async def send_message(self, runner: SubprocessRunner, message: str) -> None:
        await runner.write_json({
            "type": "user",
            "message": {"role": "user", "content": message},
            "parent_tool_use_id": None,
            "session_id": "default",
        })
