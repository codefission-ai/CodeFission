"""Shared dataclasses for the orchestrator and handlers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from models import Node, Tree


# ── Domain events (yielded by chat() async generator) ────────────────


@dataclass
class ChatNodeCreated:
    """A new child node was created for this chat."""
    node: Node
    after_id: str | None = None


@dataclass
class ChatCompleted:
    """Chat finished successfully."""
    result: ChatResult


# ── Result dataclasses ────────────────────────────────────────────────


@dataclass
class ChatContext:
    """Everything needed to start streaming a chat."""
    node_id: str
    node: Node
    workspace: Path
    sdk_message: str
    parent_session_id: str | None
    provider: str
    model: str
    max_turns: int
    auth_mode: str
    api_key: str
    after_id: str | None = None
    quoted_node_ids: list[str] = field(default_factory=list)


@dataclass
class ChatResult:
    """Returned after a successful chat completion."""
    node_id: str
    full_response: str
    git_commit: str | None = None
    files_changed: int = 0


@dataclass
class CancelResult:
    """Returned after a cancelled chat."""
    node_id: str
    saved_text: str
    active_tools: list[str] = field(default_factory=list)


@dataclass
class DeleteNodeResult:
    """Result of deleting a subtree."""
    deleted_ids: list[str]
    updated_nodes: list[Node]


@dataclass
class UpdateBaseResult:
    """Result of updating a tree's base branch/commit."""
    tree: Tree
    existing_tree_id: str | None = None
    staleness: dict = field(default_factory=lambda: {"stale": False, "commits_behind": 0})
    branches: list[str] | None = None


@dataclass
class FileListResult:
    """Result of listing files for a node."""
    node_id: str
    files: list[str]


@dataclass
class DiffResult:
    """Result of getting diff for a node."""
    node_id: str
    diff: str


@dataclass
class FileContentResult:
    """Result of reading file content for a node."""
    node_id: str
    file_path: str
    content: str


# ── Stream state (used by active_streams registry) ───────────────────


@dataclass
class StreamState:
    """Tracks state of an active chat stream."""
    node_id: str
    tree_id: str = ""
    text: str = ""
    status: str = "active"   # active | done | error
    send_fn: object = None   # async send callable (tracks current handler)
    sdk_pid: int | None = None
    stream_task: asyncio.Task | None = None
    cancelled: bool = False
