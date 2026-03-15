"""Tests for tree_service — CRUD operations on trees and nodes."""

import pytest

from store.trees import (
    create_tree, list_trees, get_tree, get_all_nodes, get_node,
    create_child_node, update_node, update_tree, delete_tree,
    get_path_to_root, find_tree,
)


# ── create_tree ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_tree_defaults(tmp_db):
    """create_tree returns a tree with default provider/model and a root node."""
    tree, root = await create_tree("Test Tree")
    assert tree.name == "Test Tree"
    assert tree.base_branch == "main"
    assert tree.provider == ""
    assert tree.model == ""
    assert root.tree_id == tree.id
    assert root.parent_id is None
    assert root.label == "root"


@pytest.mark.asyncio
async def test_create_tree_custom_params(tmp_db):
    """create_tree accepts custom provider, model, base_branch."""
    tree, root = await create_tree(
        "Custom", provider="openai", model="gpt-4",
        base_branch="develop", base_commit="abc123",
    )
    assert tree.provider == "openai"
    assert tree.model == "gpt-4"
    assert tree.base_branch == "develop"
    assert tree.base_commit == "abc123"


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
    t1, _ = await create_tree("First", repo_id="r1", base_commit="a")
    t2, _ = await create_tree("Second", repo_id="r1", base_commit="b")
    t3, _ = await create_tree("Third", repo_id="r1", base_commit="c")
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
    """update_tree persists repo_id, repo_path, repo_name."""
    tree, _ = await create_tree("T")
    await update_tree(tree.id, repo_id="abc123", repo_path="/path/to/repo", repo_name="my-repo")
    updated = await get_tree(tree.id)
    assert updated.repo_id == "abc123"
    assert updated.repo_path == "/path/to/repo"
    assert updated.repo_name == "my-repo"


@pytest.mark.asyncio
async def test_update_tree_ignores_unknown(tmp_db):
    """update_tree ignores fields not in the allowlist."""
    tree, _ = await create_tree("T")
    await update_tree(tree.id, bogus_field="hacked")
    updated = await get_tree(tree.id)
    assert updated.name == "T"  # unchanged — bogus_field not applied


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


# ── find_tree ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_tree_by_repo_commit(tmp_db):
    """find_tree locates a tree by repo_id + base_commit."""
    tree, _ = await create_tree("T", repo_id="repo1", repo_path="/path", base_commit="abc123")
    found = await find_tree("repo1", "abc123")
    assert found is not None
    assert found.id == tree.id


@pytest.mark.asyncio
async def test_find_tree_not_found(tmp_db):
    """find_tree returns None when no match."""
    await create_tree("T", repo_id="repo1", base_commit="abc123")
    assert await find_tree("repo1", "different_commit") is None
    assert await find_tree("different_repo", "abc123") is None


@pytest.mark.asyncio
async def test_find_tree_updates_repo_path(tmp_db):
    """find_tree updates repo_path when it has changed."""
    tree, _ = await create_tree("T", repo_id="repo1", repo_path="/old/path", base_commit="abc123")
    found = await find_tree("repo1", "abc123", current_repo_path="/new/path")
    assert found is not None
    assert found.id == tree.id
    # Verify the update persisted
    refetched = await get_tree(tree.id)
    assert refetched.repo_path == "/new/path"


@pytest.mark.asyncio
async def test_list_trees_cross_repo(tmp_db):
    """list_trees returns trees from all repos; filtering by repo_id works."""
    await create_tree("T1", repo_id="repo1", base_commit="aaa")
    await create_tree("T2", repo_id="repo2", base_commit="bbb")
    await create_tree("T3", repo_id="repo1", base_commit="ccc")

    all_trees = await list_trees()
    assert len(all_trees) == 3

    repo1_trees = await list_trees(repo_id="repo1")
    assert len(repo1_trees) == 2
    assert all(t.repo_id == "repo1" for t in repo1_trees)


# ── orphan trees (no repo_id) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_list_trees_excludes_orphans(tmp_db):
    """list_trees excludes trees that have no repo_id (pre-migration orphans)."""
    # Simulate a pre-migration tree with no repo_id
    await create_tree("Orphan", repo_id=None, repo_path=None)
    await create_tree("Valid", repo_id="repo1", repo_path="/path", base_commit="abc")

    all_trees = await list_trees()
    assert len(all_trees) == 1
    assert all_trees[0].name == "Valid"


@pytest.mark.asyncio
async def test_list_trees_repo_filter_ignores_orphans(tmp_db):
    """list_trees with repo_id filter never returns orphan trees."""
    await create_tree("Orphan", repo_id=None)
    await create_tree("Match", repo_id="repo1", base_commit="abc")

    trees = await list_trees(repo_id="repo1")
    assert len(trees) == 1
    assert trees[0].name == "Match"


@pytest.mark.asyncio
async def test_find_tree_ignores_orphans(tmp_db):
    """find_tree does not match trees without repo_id."""
    # Create a tree without repo_id but with a base_commit
    await create_tree("Orphan", base_commit="abc123")

    found = await find_tree("any_repo", "abc123")
    assert found is None
