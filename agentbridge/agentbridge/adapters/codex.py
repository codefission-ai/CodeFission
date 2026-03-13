"""Codex CLI adapter.

Spawns: codex exec --json --full-auto -m <model> "<prompt>"
Reads: JSONL with event tags: thread.started, turn.started, item.started,
       item.updated, item.completed, turn.completed, turn.failed, error

Codex does NOT stream text deltas — text is emitted as a single TextDelta
when item.completed fires with an AgentMessage detail type.
"""

from __future__ import annotations

import shutil
from typing import AsyncGenerator

from ..base import BaseAdapter
from ..pricing import estimate_cost_from_raw
from ..events import (
    BridgeEvent,
    SessionInit,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnComplete,
)
from ..subprocess_runner import SubprocessRunner
from ..types import ProviderType, SessionConfig

PROVIDER = ProviderType.CODEX


class CodexAdapter(BaseAdapter):
    provider = PROVIDER

    def build_command(self, config: SessionConfig) -> list[str]:
        cli = shutil.which("codex")
        if not cli:
            raise FileNotFoundError(
                "codex CLI not found. Install from https://github.com/openai/codex"
            )

        # Determine if resuming
        if config.resume_session_id:
            cmd = [cli, "exec", "resume", config.resume_session_id]
            cmd.append("--json")
            if config.prompt:
                cmd.append(config.prompt)
        else:
            cmd = [cli, "exec", "--json"]
            # Approval / sandbox
            cmd.append("--full-auto")
            if config.model:
                cmd.extend(["-m", config.model])
            cmd.extend(["-C", str(config.cwd)])

            if config.sandbox_mode:
                cmd.extend(["--sandbox", config.sandbox_mode])

            cmd.extend(config.extra_args)

            # Prepend prior context for cross-provider transfer
            prompt = config.prompt
            if config.prior_context:
                prompt = config.prior_context + "\n\n" + prompt
            cmd.append(prompt)

        return cmd

    def build_env(self, config: SessionConfig) -> dict[str, str]:
        env: dict[str, str] = {}
        env.update(config.env)
        return env

    async def stream(
        self,
        runner: SubprocessRunner,
        config: SessionConfig,
    ) -> AsyncGenerator[BridgeEvent, None]:
        session_id = ""
        # Map item IDs to names for correlating start/end
        item_names: dict[str, str] = {}

        async for data in runner.read_events():
            tag = data.get("type", "")

            # ── Thread started ─────────────────────────────────
            if tag == "thread.started":
                session_id = data.get("thread_id", str(runner.pid))
                yield SessionInit(
                    session_id=session_id, provider=PROVIDER.value, raw=data
                )

            # ── Item started ───────────────────────────────────
            elif tag == "item.started":
                item = data.get("item", {})
                item_id = item.get("id", "")
                # Codex puts type and fields directly on item (not nested under "details")
                item_type = item.get("type", "")

                if item_type == "command_execution":
                    name = "bash"
                    item_names[item_id] = name
                    yield ToolStart(
                        tool_call_id=item_id,
                        name=name,
                        arguments={"command": item.get("command", "")},
                        provider=PROVIDER.value,
                        raw=data,
                    )
                elif item_type == "mcp_tool_call":
                    name = item.get("tool", item.get("server", "mcp"))
                    item_names[item_id] = name
                    yield ToolStart(
                        tool_call_id=item_id,
                        name=name,
                        arguments=item.get("arguments", {}),
                        provider=PROVIDER.value,
                        raw=data,
                    )
                elif item_type == "file_change":
                    name = "file_edit"
                    item_names[item_id] = name
                    changes = item.get("changes", [])
                    desc = ", ".join(
                        f"{c.get('kind', '?')} {c.get('path', '?')}" for c in changes
                    )
                    yield ToolStart(
                        tool_call_id=item_id,
                        name=name,
                        arguments={"changes": desc},
                        provider=PROVIDER.value,
                        raw=data,
                    )

            # ── Item completed ─────────────────────────────────
            elif tag == "item.completed":
                item = data.get("item", {})
                item_id = item.get("id", "")
                item_type = item.get("type", "")

                if item_type == "agent_message":
                    text = item.get("text", "")
                    if text:
                        yield TextDelta(
                            text=text, provider=PROVIDER.value, raw=data
                        )
                elif item_type == "command_execution":
                    name = item_names.pop(item_id, "bash")
                    output = item.get("aggregated_output", "")
                    exit_code = item.get("exit_code")
                    is_error = exit_code is not None and exit_code != 0
                    yield ToolEnd(
                        tool_call_id=item_id,
                        name=name,
                        result=output,
                        is_error=is_error,
                        provider=PROVIDER.value,
                        raw=data,
                    )
                elif item_type == "mcp_tool_call":
                    name = item_names.pop(item_id, "mcp")
                    result_content = item.get("result", {})
                    if isinstance(result_content, dict):
                        output = str(result_content.get("content", ""))
                    else:
                        output = str(result_content)
                    error = item.get("error")
                    yield ToolEnd(
                        tool_call_id=item_id,
                        name=name,
                        result=output,
                        is_error=bool(error),
                        provider=PROVIDER.value,
                        raw=data,
                    )
                elif item_type == "file_change":
                    name = item_names.pop(item_id, "file_edit")
                    changes = item.get("changes", [])
                    desc = ", ".join(
                        f"{c.get('kind', '?')} {c.get('path', '?')}" for c in changes
                    )
                    yield ToolEnd(
                        tool_call_id=item_id,
                        name=name,
                        result=desc,
                        is_error=False,
                        provider=PROVIDER.value,
                        raw=data,
                    )

            # ── Turn completed ─────────────────────────────────
            elif tag == "turn.completed":
                usage_data = data.get("usage", {})
                token_usage = {
                    "input_tokens": usage_data.get("input_tokens", 0),
                    "output_tokens": usage_data.get("output_tokens", 0),
                    "cached_input_tokens": usage_data.get("cached_input_tokens", 0),
                } if usage_data else None

                model = config.model or "gpt-5.3-codex"
                cost = estimate_cost_from_raw(model, data)

                yield TurnComplete(
                    session_id=session_id,
                    is_error=False,
                    cost_usd=cost,
                    token_usage=token_usage,
                    provider=PROVIDER.value,
                    raw=data,
                )

            elif tag == "turn.failed":
                yield TurnComplete(
                    session_id=session_id,
                    is_error=True,
                    provider=PROVIDER.value,
                    raw=data,
                )

            elif tag == "error":
                yield TurnComplete(
                    session_id=session_id,
                    is_error=True,
                    provider=PROVIDER.value,
                    raw=data,
                )
