"""Tests for chat_service — event types, system prompt, and workspace context."""

import pytest
from pathlib import Path

from config import set_project_path
from store.ai import (
    TextDelta, ToolStart, ToolEnd, SessionInit,
    _build_system_prompt, _build_workspace_context,
)
from models import Node, Tree


@pytest.fixture(autouse=True)
def _set_fake_project():
    """Set a fake project path so _build_workspace_context can call get_project_path()."""
    set_project_path(Path("/fake/project"))


# ── Event dataclass tests ────────────────────────────────────────────

def test_text_delta():
    d = TextDelta("hello")
    assert d.kind == "text_delta"
    assert d.text == "hello"


def test_tool_start_defaults():
    ts = ToolStart(tool_call_id="tc-1", name="bash")
    assert ts.kind == "tool_start"
    assert ts.arguments == {}


def test_tool_start_with_args():
    ts = ToolStart("tc-2", "read_file", {"path": "/foo"})
    assert ts.arguments == {"path": "/foo"}


def test_tool_end():
    te = ToolEnd("tc-1", "bash", "output", is_error=False)
    assert te.kind == "tool_end"
    assert te.is_error is False


def test_tool_end_error():
    te = ToolEnd("tc-1", "bash", "failed", is_error=True)
    assert te.is_error is True


def test_session_init():
    si = SessionInit("sess-abc")
    assert si.kind == "session_init"
    assert si.session_id == "sess-abc"


# ── System prompt tests (STATIC — no node-specific data) ─────────────

def _make_node(**kwargs) -> Node:
    defaults = dict(
        id="node-1", tree_id="tree-1", parent_id=None,
        user_message="", assistant_response="", label="root",
        status="idle", created_at="", children_ids=[],
        git_branch=None, git_commit=None, session_id=None,
    )
    defaults.update(kwargs)
    return Node(**defaults)


def _make_tree(**kwargs) -> Tree:
    defaults = dict(
        id="tree-1", name="Test", created_at="",
        root_node_id="node-1", provider="claude-code",
        model="claude-sonnet-4-6",
    )
    defaults.update(kwargs)
    return Tree(**defaults)


def test_system_prompt_basic():
    """System prompt is non-empty without tree context."""
    prompt = _build_system_prompt()
    assert "CodeFission" in prompt
    assert len(prompt) > 50


def test_system_prompt_with_tree():
    """With tree, includes workspace rules and artifact instructions."""
    tree = _make_tree()
    prompt = _build_system_prompt(tree=tree)
    assert "FILESYSTEM RULES" in prompt
    assert "_artifacts/" in prompt
    assert "automatically committed" in prompt


def test_system_prompt_no_node_specific_data():
    """System prompt must NOT contain node-specific data (workspace path, branch, commit).
    These go in the user message to maximize cache hits."""
    tree = _make_tree()
    prompt = _build_system_prompt(tree=tree)
    # Should NOT contain any node-specific paths or IDs
    assert "worktrees/" not in prompt
    assert "ct-" not in prompt
    assert "abc123" not in prompt


def test_system_prompt_with_instructions():
    """Tree instructions are appended to system prompt."""
    tree = _make_tree()
    prompt = _build_system_prompt(tree=tree, tree_instructions="Use Python 3.10 syntax")
    assert "Use Python 3.10 syntax" in prompt
    assert "Tree Instructions" in prompt


def test_system_prompt_stable_across_nodes():
    """System prompt is identical for different nodes in the same tree."""
    tree = _make_tree()
    prompt1 = _build_system_prompt(tree=tree, tree_instructions="Be concise")
    prompt2 = _build_system_prompt(tree=tree, tree_instructions="Be concise")
    assert prompt1 == prompt2


def test_system_prompt_write_restriction():
    """Prompt forbids writing outside workspace."""
    tree = _make_tree()
    prompt = _build_system_prompt(tree=tree)
    assert "NEVER" in prompt
    assert "outside your workspace" in prompt


def test_system_prompt_artifacts():
    """Prompt instructs agents to use _artifacts/ for generated output."""
    tree = _make_tree()
    prompt = _build_system_prompt(tree=tree)
    assert "_artifacts/" in prompt
    assert "_artifacts/plot.png" in prompt


# ── Workspace context tests (DYNAMIC — per-node) ─────────────────────

def test_workspace_context_root():
    """Root node workspace context mentions root and project path."""
    node = _make_node()
    tree = _make_tree(base_branch="main")
    ws = Path("/fake/workspace")
    ctx = _build_workspace_context(ws, node, tree)
    assert "/fake/workspace" in ctx
    assert "Root node" in ctx
    assert "main" in ctx


def test_workspace_context_child():
    """Child node includes branch and commit."""
    node = _make_node(parent_id="parent-1", git_branch="ct-node-1", git_commit="abc123def456")
    tree = _make_tree()
    ws = Path("/fake/worktrees/node-1")
    ctx = _build_workspace_context(ws, node, tree)
    assert "/fake/worktrees/node-1" in ctx
    assert "ct-node-1" in ctx
    assert "abc123def456"[:12] in ctx
    assert "Branch node" in ctx


def test_workspace_context_changes_per_node():
    """Different nodes produce different workspace contexts."""
    tree = _make_tree()
    node_a = _make_node(id="a", git_branch="ct-a", git_commit="aaa111")
    node_b = _make_node(id="b", parent_id="a", git_branch="ct-b", git_commit="bbb222")
    ctx_a = _build_workspace_context(Path("/ws/a"), node_a, tree)
    ctx_b = _build_workspace_context(Path("/ws/b"), node_b, tree)
    assert ctx_a != ctx_b
    assert "/ws/a" in ctx_a
    assert "/ws/b" in ctx_b
