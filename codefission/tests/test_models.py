"""Tests for Pydantic models — defaults, serialization, mutable defaults."""

from models import Node, Tree


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
    assert t.provider == ""
    assert t.model == ""
    assert t.repo_id is None
    assert t.repo_path is None
    assert t.repo_name is None
    assert t.root_node_id is None


def test_tree_serialization():
    """Tree.model_dump() includes all fields."""
    t = Tree(id="t1", name="Test", repo_id="abc", repo_path="/path", repo_name="my-repo")
    d = t.model_dump()
    assert d["repo_id"] == "abc"
    assert d["repo_path"] == "/path"
    assert d["repo_name"] == "my-repo"
    assert "root_node_id" in d


def test_tree_empty_provider_model():
    """Tree provider and model default to empty string (inherit from global)."""
    t = Tree(id="t1", name="Test")
    assert t.provider == ""
    assert t.model == ""
