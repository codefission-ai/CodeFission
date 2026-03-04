"""Chat service — uses Claude Agent SDK with session forking.

Each node gets its own session_id. Child nodes fork from their parent's session,
reusing the prompt cache. Yields structured ChatEvent objects so the caller can
forward them to the frontend as distinct WebSocket messages.
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
    ResultMessage,
    AssistantMessage,
    ToolResultBlock,
    query,
)
from claude_agent_sdk.types import StreamEvent

from services.tree_service import get_tree, get_node

log = logging.getLogger(__name__)

# When True, uses ANTHROPIC_API_KEY; when False, uses Claude Code subscription.
USE_API = False


# ── Structured events yielded to caller ──────────────────────────────

@dataclass
class ChatEvent:
    kind: str  # "text_delta" | "tool_start" | "tool_end" | "session_init"


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
class SessionInit(ChatEvent):
    """Yielded once when the session_id is known (from first StreamEvent)."""
    session_id: str = ""

    def __init__(self, session_id: str):
        self.kind = "session_init"
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


def _build_system_prompt(node, tree=None, workspace: Path | None = None) -> str:
    parts = [
        "You are a helpful AI coding assistant in RepoEvolve, a tree-structured "
        "development tool where each node is an isolated git worktree. "
        "Be concise and helpful."
    ]

    # Repo / workspace context
    if tree and workspace:
        parts.append("\n\n## Workspace")
        parts.append(f"\nYour working directory is: {workspace}")
        parts.append(f"\nYou MUST NOT write files outside this directory.")
        parts.append(f"\nYou may read files anywhere on the system for reference.")
        if tree.repo_source:
            parts.append(f"\nThis project was cloned from: {tree.repo_source}")
            parts.append(f"\nYou can read files in {tree.repo_source} for reference, but write only in your working directory.")
        if tree.repo_mode == "new":
            parts.append("\nThis is a fresh empty repository — create any files needed from scratch.")

        is_root = not node.parent_id
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
        if node.git_branch:
            parts.append(f"\nGit branch: {node.git_branch}")
        if node.git_commit:
            parts.append(f"\nCurrent commit: {node.git_commit}")

        parts.append(
            "\n\nAll your file changes are automatically committed after each response. "
            "Focus on writing code and making changes — git operations are handled for you."
            "\n\nIMPORTANT: Other branches in this repo belong to sibling conversation nodes "
            "and are completely independent. Do NOT use `git log --all`, `git branch`, "
            "`git show` on other branches, or reference any branch other than your own. "
            "Only interact with files in your working directory."
        )

    return "".join(parts)


# ── Main streaming function ──────────────────────────────────────────

async def stream_chat(
    node_id: str,
    user_message: str,
    workspace: Path,
    parent_session_id: str | None = None,
) -> AsyncGenerator[ChatEvent, None]:
    """Stream a chat response for a node, yielding structured ChatEvents.

    If parent_session_id is provided, the session forks from it (reusing cache).
    Otherwise a new session is created.
    """
    node = await get_node(node_id)
    if not node:
        return

    tree = await get_tree(node.tree_id)
    if not tree:
        return

    system_prompt = _build_system_prompt(node, tree=tree, workspace=workspace)

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

    # Session forking: if parent has a session file, fork from it
    if parent_session_id:
        from services.workspace_service import session_file_exists
        if session_file_exists(workspace, parent_session_id):
            options.resume = parent_session_id
            options.fork_session = True
        else:
            log.info("Parent session file missing, starting fresh session for node %s", node_id)

    # Track pending tool calls to pair start/end
    _pending_tool: dict | None = None
    _session_id_yielded = False

    try:
        async for msg in query(prompt=user_message, options=options):

            # ── Token-level streaming events ─────────────────
            if isinstance(msg, StreamEvent):
                # Capture session_id from the first stream event
                if not _session_id_yielded and msg.session_id:
                    _session_id_yielded = True
                    yield SessionInit(msg.session_id)

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
                # Capture session_id from result if we haven't yet
                if not _session_id_yielded and msg.session_id:
                    yield SessionInit(msg.session_id)
                return

    except BaseException as exc:
        log.exception("Chat error for node %s", node_id)
        raise
