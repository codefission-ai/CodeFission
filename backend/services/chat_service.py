"""Chat service — uses Claude Agent SDK (subscription or API key).

Yields structured ChatEvent objects (text deltas, tool calls) so the caller
can forward them to the frontend as distinct WebSocket messages.
"""

from __future__ import annotations

import json
import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    AssistantMessage,
    ToolResultBlock,
)
from claude_agent_sdk.types import StreamEvent

from services.tree_service import get_path_to_root, get_tree, get_node

log = logging.getLogger(__name__)

# When True, uses ANTHROPIC_API_KEY; when False, uses Claude Code subscription.
USE_API = False


# ── Structured events yielded to caller ──────────────────────────────

@dataclass
class ChatEvent:
    kind: str  # "text_delta" | "tool_start" | "tool_end" | "result"


@dataclass
class TextDelta(ChatEvent):
    text: str = ""

    def __init__(self, text: str):
        self.kind = "text_delta"
        self.text = text


@dataclass
class ToolStart(ChatEvent):
    tool_call_id: str = ""
    name: str = ""
    arguments: dict = field(default_factory=dict)

    def __init__(self, tool_call_id: str, name: str, arguments: dict | None = None):
        self.kind = "tool_start"
        self.tool_call_id = tool_call_id
        self.name = name
        self.arguments = arguments or {}


@dataclass
class ToolEnd(ChatEvent):
    tool_call_id: str = ""
    name: str = ""
    result: str = ""
    is_error: bool = False

    def __init__(self, tool_call_id: str, name: str, result: str = "", is_error: bool = False):
        self.kind = "tool_end"
        self.tool_call_id = tool_call_id
        self.name = name
        self.result = result
        self.is_error = is_error


@dataclass
class ChatResult(ChatEvent):
    session_id: str | None = None

    def __init__(self, session_id: str | None = None):
        self.kind = "result"
        self.session_id = session_id


# ── Helpers ──────────────────────────────────────────────────────────

def _sdk_env() -> dict[str, str]:
    """Build env dict for the SDK subprocess."""
    env = {"CLAUDECODE": ""}
    if USE_API:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if key:
            env["ANTHROPIC_API_KEY"] = key
    return env


def _build_system_prompt(path_nodes, tree=None, workspace: Path | None = None) -> str:
    parts = [
        "You are a helpful AI coding assistant in RepoEvolve, a tree-structured "
        "development tool where each node is an isolated git worktree. "
        "Be concise and helpful."
    ]

    # Repo / workspace context
    if tree and tree.repo_mode != "none" and workspace:
        parts.append("\n\n## Workspace")
        parts.append(f"\nYour working directory is: {workspace}")
        parts.append(f"\nYou MUST NOT write files outside this directory.")
        parts.append(f"\nYou may read files anywhere on the system for reference.")
        if tree.repo_source:
            parts.append(f"\nThis project was cloned from: {tree.repo_source}")
            parts.append(f"\nYou can read files in {tree.repo_source} for reference, but write only in your working directory.")
        if tree.repo_mode == "new":
            parts.append("\nThis is a fresh empty repository — create any files needed from scratch.")

        current_node = path_nodes[-1] if path_nodes else None
        is_root = current_node and not current_node.parent_id
        if is_root:
            parts.append(
                "\nYou are working on the root node (main branch). "
                "Your changes here form the base that child branches evolve from."
            )
        else:
            parts.append(
                "\nYou are working on a branch node (git worktree). "
                "This worktree was forked from the parent node's commit. "
                "Your changes here are isolated and do not affect the parent or sibling branches."
            )
        if current_node and current_node.git_branch:
            parts.append(f"\nGit branch: {current_node.git_branch}")
        if current_node and current_node.git_commit:
            parts.append(f"\nCurrent commit: {current_node.git_commit}")

        parts.append(
            "\n\nAll your file changes are automatically committed after each response. "
            "Focus on writing code and making changes — git operations are handled for you."
        )

    # Conversation history from ancestor nodes
    ancestors = path_nodes[:-1] if len(path_nodes) > 1 else []
    if ancestors:
        history_parts = []
        for node in ancestors:
            if node.user_message:
                history_parts.append(f"User: {node.user_message}")
            if node.assistant_response:
                history_parts.append(f"Assistant: {node.assistant_response}")
        if history_parts:
            parts.append("\n\n## Conversation history (continue naturally from here)\n\n" + "\n\n".join(history_parts))
    return "".join(parts)


# ── Main streaming function ──────────────────────────────────────────

async def stream_chat(node_id: str, user_message: str, workspace: Path) -> AsyncGenerator[ChatEvent, None]:
    """Stream a chat response for a node, yielding structured ChatEvents."""
    node = await get_node(node_id)
    if not node:
        return

    tree = await get_tree(node.tree_id)
    if not tree:
        return

    path = await get_path_to_root(node_id)
    system_prompt = _build_system_prompt(path, tree=tree, workspace=workspace)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=tree.model,
        cwd=str(workspace),
        max_turns=25,
        permission_mode="bypassPermissions",
        include_partial_messages=True,
        env=_sdk_env(),
        debug_stderr=open(os.devnull, "w"),
    )

    # Track pending tool calls to pair start/end
    _pending_tool: dict | None = None

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_message)
            async for msg in client.receive_response():

                # ── Token-level streaming events ─────────────────
                if isinstance(msg, StreamEvent):
                    evt = msg.event
                    evt_type = evt.get("type", "")

                    if evt_type == "content_block_delta":
                        delta = evt.get("delta", {})
                        delta_type = delta.get("type", "")
                        if delta_type == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield TextDelta(text)
                        elif delta_type == "input_json_delta" and _pending_tool:
                            _pending_tool["input_json"] += delta.get("partial_json", "")

                    elif evt_type == "content_block_start":
                        block = evt.get("content_block", {})
                        if block.get("type") == "tool_use":
                            _pending_tool = {
                                "id": block["id"],
                                "name": block["name"],
                                "input_json": "",
                            }
                            yield ToolStart(
                                tool_call_id=block["id"],
                                name=block["name"],
                            )

                    elif evt_type == "content_block_stop":
                        if _pending_tool:
                            # Parse accumulated JSON args
                            try:
                                args = (
                                    json.loads(_pending_tool["input_json"])
                                    if _pending_tool["input_json"]
                                    else {}
                                )
                            except json.JSONDecodeError:
                                args = {}
                            # Re-yield start with parsed arguments
                            yield ToolStart(
                                tool_call_id=_pending_tool["id"],
                                name=_pending_tool["name"],
                                arguments=args,
                            )
                            _pending_tool = None

                # ── Completed assistant turn (contains tool results) ──
                elif isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolResultBlock):
                            result_text = ""
                            if isinstance(block.content, str):
                                result_text = block.content
                            elif block.content:
                                result_text = str(block.content)
                            yield ToolEnd(
                                tool_call_id=block.tool_use_id,
                                name="",  # we don't have the name here
                                result=result_text,
                                is_error=bool(block.is_error),
                            )

                # ── Final result ──────────────────────────────────
                elif isinstance(msg, ResultMessage):
                    # Don't yield here — returning ends the generator
                    # cleanly without triggering GeneratorExit inside
                    # the async with block.
                    return

    except BaseException as exc:
        log.exception("Chat error for node %s", node_id)
        raise
