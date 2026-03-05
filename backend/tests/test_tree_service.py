"""Tests for tree_service — CRUD operations on trees and nodes."""

import pytest

from services.tree_service import (
    create_tree, list_trees, get_tree, get_all_nodes, get_node,
    create_child_node, update_node, update_tree, delete_tree,
    get_path_to_root,
)


# ── create_tree ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_tree_defaults(tmp_db):
    """create_tree returns a tree with default provider/model and a root node."""
    tree, root = await create_tree("Test Tree")
    assert tree.name == "Test Tree"
    assert tree.repo_mode == "new"
    assert tree.provider == ""
    assert tree.model == ""
    assert root.tree_id == tree.id
    assert root.parent_id is None
    assert root.label == "root"


@pytest.mark.asyncio
async def test_create_tree_custom_params(tmp_db):
    """create_tree accepts custom provider, model, repo_mode."""
    tree, root = await create_tree(
        "Custom", provider="openai", model="gpt-4", repo_mode="local",
        repo_source="/some/path",
    )
    assert tree.provider == "openai"
    assert tree.model == "gpt-4"
    assert tree.repo_mode == "local"
    assert tree.repo_source == "/some/path"


@pytest.mark.asyncio
async def test_create_tree_unique_ids(tmp_db):
    """Each tree and root node gets a unique ID."""
    ids = set()
    for i in range(20):
        tree, root = await create_tree(f"Tree {i}")
        assert tree.id not in ids
        assert root.id not in ids
        ids.add(tree.id)
        ids.add(root.id)


# ── list_trees ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_trees_empty(tmp_db):
    """list_trees returns empty list when no trees exist."""
    trees = await list_trees()
    assert trees == []


@pytest.mark.asyncio
async def test_list_trees_ordering(tmp_db):
    """list_trees returns trees in reverse chronological order."""
    t1, _ = await create_tree("First")
    t2, _ = await create_tree("Second")
    t3, _ = await create_tree("Third")
    trees = await list_trees()
    assert len(trees) == 3
    # Most recent first
    assert trees[0].name == "Third"
    assert trees[2].name == "First"


# ── get_tree ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_tree_exists(tmp_db):
    """get_tree returns tree with root_node_id populated."""
    tree, root = await create_tree("My Tree")
    fetched = await get_tree(tree.id)
    assert fetched is not None
    assert fetched.id == tree.id
    assert fetched.root_node_id == root.id


@pytest.mark.asyncio
async def test_get_tree_not_found(tmp_db):
    """get_tree returns None for nonexistent ID."""
    result = await get_tree("nonexistent")
    assert result is None


# ── get_node ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_node_exists(tmp_db):
    """get_node returns the node with all fields."""
    tree, root = await create_tree("T")
    node = await get_node(root.id)
    assert node is not None
    assert node.id == root.id
    assert node.tree_id == tree.id
    assert node.status == "idle"
    assert node.children_ids == []


@pytest.mark.asyncio
async def test_get_node_not_found(tmp_db):
    """get_node returns None for nonexistent ID."""
    assert await get_node("nonexistent") is None


@pytest.mark.asyncio
async def test_get_node_children_ids(tmp_db):
    """get_node populates children_ids from DB."""
    tree, root = await create_tree("T")
    c1 = await create_child_node(root.id, "child1")
    c2 = await create_child_node(root.id, "child2")
    parent = await get_node(root.id)
    assert set(parent.children_ids) == {c1.id, c2.id}


# ── create_child_node ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_child_node(tmp_db):
    """create_child_node creates a node parented to the given node."""
    tree, root = await create_tree("T")
    child = await create_child_node(root.id, "my child")
    assert child.parent_id == root.id
    assert child.tree_id == tree.id
    assert child.label == "my child"
    assert child.status == "idle"


@pytest.mark.asyncio
async def test_create_child_node_invalid_parent(tmp_db):
    """create_child_node raises ValueError for nonexistent parent."""
    with pytest.raises(ValueError, match="not found"):
        await create_child_node("nonexistent", "label")


@pytest.mark.asyncio
async def test_create_child_deep_nesting(tmp_db):
    """Can create deeply nested child chains."""
    tree, root = await create_tree("T")
    parent_id = root.id
    for i in range(10):
        child = await create_child_node(parent_id, f"level-{i}")
        assert child.parent_id == parent_id
        parent_id = child.id


# ── update_node ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_node_fields(tmp_db):
    """update_node persists allowed fields."""
    tree, root = await create_tree("T")
    await update_node(root.id,
        user_message="hello",
        assistant_response="world",
        label="updated",
        status="done",
        git_branch="main",
        git_commit="abc123",
        session_id="sess-1",
    )
    node = await get_node(root.id)
    assert node.user_message == "hello"
    assert node.assistant_response == "world"
    assert node.label == "updated"
    assert node.status == "done"
    assert node.git_branch == "main"
    assert node.git_commit == "abc123"
    assert node.session_id == "sess-1"


@pytest.mark.asyncio
async def test_update_node_ignores_unknown_fields(tmp_db):
    """update_node silently ignores fields not in the allowlist."""
    tree, root = await create_tree("T")
    # Should not raise
    await update_node(root.id, bogus_field="value", id="hacked")
    node = await get_node(root.id)
    assert node.id == root.id  # id unchanged


@pytest.mark.asyncio
async def test_update_node_partial(tmp_db):
    """update_node can update a single field without touching others."""
    tree, root = await create_tree("T")
    await update_node(root.id, user_message="hello")
    await update_node(root.id, status="active")
    node = await get_node(root.id)
    assert node.user_message == "hello"
    assert node.status == "active"


# ── update_tree ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_tree(tmp_db):
    """update_tree persists repo_mode and repo_source."""
    tree, _ = await create_tree("T")
    await update_tree(tree.id, repo_mode="local", repo_source="/path")
    updated = await get_tree(tree.id)
    assert updated.repo_mode == "local"
    assert updated.repo_source == "/path"


@pytest.mark.asyncio
async def test_update_tree_ignores_unknown(tmp_db):
    """update_tree ignores fields not in the allowlist."""
    tree, _ = await create_tree("T")
    await update_tree(tree.id, name="hacked")
    updated = await get_tree(tree.id)
    assert updated.name == "T"  # unchanged


# ── delete_tree ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_tree(tmp_db, tmp_workspaces):
    """delete_tree removes tree and all nodes from DB."""
    tree, root = await create_tree("T")
    child = await create_child_node(root.id, "c")
    await delete_tree(tree.id)
    assert await get_tree(tree.id) is None
    assert await get_node(root.id) is None
    assert await get_node(child.id) is None


@pytest.mark.asyncio
async def test_delete_tree_nonexistent(tmp_db, tmp_workspaces):
    """delete_tree on nonexistent tree doesn't raise."""
    await delete_tree("nonexistent")  # should not raise


# ── get_all_nodes ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_all_nodes(tmp_db):
    """get_all_nodes returns all nodes for a tree with children_ids populated."""
    tree, root = await create_tree("T")
    c1 = await create_child_node(root.id, "c1")
    c2 = await create_child_node(root.id, "c2")
    gc = await create_child_node(c1.id, "gc")

    nodes = await get_all_nodes(tree.id)
    assert len(nodes) == 4
    by_id = {n.id: n for n in nodes}
    assert set(by_id[root.id].children_ids) == {c1.id, c2.id}
    assert by_id[c1.id].children_ids == [gc.id]
    assert by_id[c2.id].children_ids == []
    assert by_id[gc.id].children_ids == []


@pytest.mark.asyncio
async def test_get_all_nodes_empty_tree(tmp_db):
    """get_all_nodes returns empty list for nonexistent tree."""
    nodes = await get_all_nodes("nonexistent")
    assert nodes == []


# ── get_path_to_root ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_path_to_root(tmp_db):
    """get_path_to_root returns [root, ..., node] path."""
    tree, root = await create_tree("T")
    c1 = await create_child_node(root.id, "c1")
    gc = await create_child_node(c1.id, "gc")

    path = await get_path_to_root(gc.id)
    assert len(path) == 3
    assert path[0].id == root.id
    assert path[1].id == c1.id
    assert path[2].id == gc.id


@pytest.mark.asyncio
async def test_get_path_to_root_single(tmp_db):
    """get_path_to_root for root node returns [root]."""
    tree, root = await create_tree("T")
    path = await get_path_to_root(root.id)
    assert len(path) == 1
    assert path[0].id == root.id
