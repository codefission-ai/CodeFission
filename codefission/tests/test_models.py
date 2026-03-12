"""Tests for Pydantic models — defaults, serialization, mutable defaults."""

from models import Node, Tree, DEFAULT_PROVIDER, DEFAULT_MODEL


# ── Node ─────────────────────────────────────────────────────────────

def test_node_defaults():
    """Node has sensible defaults for optional fields."""
    n = Node(id="n1", tree_id="t1")
    assert n.parent_id is None
    assert n.user_message == ""
    assert n.assistant_response == ""
    assert n.label == ""
    assert n.status == "idle"
    assert n.children_ids == []
    assert n.git_branch is None
    assert n.git_commit is None
    assert n.session_id is None


def test_node_mutable_default_isolation():
    """Each Node gets its own children_ids list (no shared mutable default)."""
    n1 = Node(id="n1", tree_id="t1")
    n2 = Node(id="n2", tree_id="t1")
    n1.children_ids.append("child")
    assert n2.children_ids == []  # must not be affected


def test_node_serialization():
    """Node.model_dump() includes all fields."""
    n = Node(id="n1", tree_id="t1", label="test", git_branch="main")
    d = n.model_dump()
    assert d["id"] == "n1"
    assert d["label"] == "test"
    assert d["git_branch"] == "main"
    assert "children_ids" in d


# ── Tree ─────────────────────────────────────────────────────────────

def test_tree_defaults():
    """Tree has correct defaults."""
    t = Tree(id="t1", name="Test")
    assert t.provider == DEFAULT_PROVIDER
    assert t.model == DEFAULT_MODEL
    assert t.repo_mode == "new"
    assert t.repo_source is None
    assert t.root_node_id is None


def test_tree_serialization():
    """Tree.model_dump() includes all fields."""
    t = Tree(id="t1", name="Test", repo_mode="local", repo_source="/path")
    d = t.model_dump()
    assert d["repo_mode"] == "local"
    assert d["repo_source"] == "/path"
    assert "root_node_id" in d


def test_default_constants():
    """DEFAULT_PROVIDER and DEFAULT_MODEL are empty (inherit from global)."""
    assert DEFAULT_PROVIDER == ""
    assert DEFAULT_MODEL == ""
