"""Data models — all types shared across the application.

Pydantic models: Node, Tree (DB-backed entities).
Dataclasses: ChatContext, ChatResult, ChatNodeCreated, ChatCompleted,
  CancelResult, DeleteNodeResult, UpdateBaseResult, StreamState,
  FileListResult, DiffResult, FileContentResult.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

# Default provider/model for new trees (empty = use global default)
DEFAULT_PROVIDER = ""
DEFAULT_MODEL = ""


class Node(BaseModel):
    id: str
    tree_id: str
    parent_id: str | None = None
    user_message: str = ""
    assistant_response: str = ""
    label: str = ""
    status: str = "idle"
    created_at: str = ""
    children_ids: list[str] = []
    git_branch: str | None = None
    git_commit: str | None = None
    session_id: str | None = None
    provider: str | None = None
    model: str | None = None
    created_by: str = "human"
    quoted_node_ids: list[str] = []


class Tree(BaseModel):
    id: str
    name: str
    created_at: str = ""
    root_node_id: str | None = None
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    skill: str = ""
    notes: str = "[]"  # JSON array of {id, text, x, y, width, height}
    base_branch: str = "main"
    base_commit: str | None = None
    repo_id: str | None = None       # SHA of initial commit (repo identity)
    repo_path: str | None = None     # last known abs path (display + workspace resolution)
    repo_name: str | None = None     # display name (from git remote or dirname)


# ── Audit log action ─────────────────────────────────────────────────


@dataclass
class Action:
    """A single recorded action in the audit log."""
    id: str
    seq: int
    ts: str
    tree_id: str | None
    node_id: str | None
    kind: str
    params: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    source: str = "gui"  # "gui" or "cli"


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
