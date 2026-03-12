"""Tests for chat_service — event types and system prompt building."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from services.chat_service import (
    TextDelta, ToolStart, ToolEnd, SessionInit, _build_system_prompt,
)
from models import Node, Tree


# ── Event dataclass tests ────────────────────────────────────────────

def test_text_delta():
    """TextDelta stores text and sets kind correctly."""
    d = TextDelta("hello")
    assert d.kind == "text_delta"
    assert d.text == "hello"


def test_tool_start_defaults():
    """ToolStart has empty arguments by default."""
    ts = ToolStart(tool_call_id="tc-1", name="bash")
    assert ts.kind == "tool_start"
    assert ts.tool_call_id == "tc-1"
    assert ts.name == "bash"
    assert ts.arguments == {}


def test_tool_start_with_args():
    """ToolStart can carry parsed arguments."""
    ts = ToolStart("tc-2", "read_file", {"path": "/foo"})
    assert ts.arguments == {"path": "/foo"}


def test_tool_end():
    """ToolEnd captures result and error flag."""
    te = ToolEnd("tc-1", "bash", "output", is_error=False)
    assert te.kind == "tool_end"
    assert te.result == "output"
    assert te.is_error is False


def test_tool_end_error():
    """ToolEnd with is_error=True."""
    te = ToolEnd("tc-1", "bash", "failed", is_error=True)
    assert te.is_error is True


def test_session_init():
    """SessionInit stores the session ID."""
    si = SessionInit("sess-abc")
    assert si.kind == "session_init"
    assert si.session_id == "sess-abc"


# ── System prompt building ───────────────────────────────────────────

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
        root_node_id="node-1", provider="anthropic",
        model="claude-sonnet-4-6", repo_mode="new", repo_source=None,
    )
    defaults.update(kwargs)
    return Tree(**defaults)


def test_system_prompt_basic():
    """System prompt is non-empty without tree/workspace context."""
    node = _make_node()
    prompt = _build_system_prompt(node)
    assert "CodeFission" in prompt
    assert len(prompt) > 50


def test_system_prompt_root_node_with_workspace():
    """Root node prompt mentions main branch and working directory."""
    node = _make_node()
    tree = _make_tree(repo_mode="new")
    ws = Path("/fake/workspace")
    prompt = _build_system_prompt(node, tree=tree, workspace=ws)
    assert "/fake/workspace" in prompt
    assert "root node" in prompt
    assert "main branch" in prompt
    assert "automatically committed" in prompt


def test_system_prompt_child_node():
    """Child node prompt mentions branch/worktree."""
    node = _make_node(parent_id="parent-1", git_branch="ct-node-1", git_commit="abc123")
    tree = _make_tree()
    ws = Path("/fake/workspace")
    prompt = _build_system_prompt(node, tree=tree, workspace=ws)
    assert "branch node" in prompt
    assert "ct-node-1" in prompt
    assert "abc123" in prompt
    assert "isolated" in prompt


def test_system_prompt_cloned_repo():
    """Prompt mentions clone source for local/url repos."""
    node = _make_node()
    tree = _make_tree(repo_mode="local", repo_source="/home/user/project")
    ws = Path("/fake/workspace")
    prompt = _build_system_prompt(node, tree=tree, workspace=ws)
    assert "/home/user/project" in prompt
    assert "cloned from" in prompt


def test_system_prompt_new_repo():
    """Prompt mentions fresh empty repository for new repos."""
    node = _make_node()
    tree = _make_tree(repo_mode="new")
    ws = Path("/fake/workspace")
    prompt = _build_system_prompt(node, tree=tree, workspace=ws)
    assert "fresh empty repository" in prompt


def test_system_prompt_no_workspace():
    """Without workspace, no workspace section is added."""
    node = _make_node()
    tree = _make_tree()
    prompt = _build_system_prompt(node, tree=tree, workspace=None)
    assert "working directory" not in prompt


def test_system_prompt_write_restriction():
    """Prompt forbids writing outside workspace."""
    node = _make_node()
    tree = _make_tree()
    ws = Path("/fake/workspace")
    prompt = _build_system_prompt(node, tree=tree, workspace=ws)
    assert "NEVER write to" in prompt
    assert "outside your workspace" in prompt


def test_system_prompt_artifacts():
    """Prompt instructs agents to use _artifacts/ for generated output files."""
    node = _make_node()
    tree = _make_tree()
    ws = Path("/fake/workspace")
    prompt = _build_system_prompt(node, tree=tree, workspace=ws)
    assert "_artifacts/" in prompt
    assert "_artifacts/plot.png" in prompt
