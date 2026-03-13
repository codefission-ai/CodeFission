"""Phase 1B — Test Orchestrator methods extracted from handlers.

Tests for operations that are moving from handlers.py to the Orchestrator:
  - delete_node (leaf, subtree, root rejection, streaming rejection,
    settings cleanup, worktree cleanup)
  - update_base (commit update, invalid commit, children guard)
  - update_global_settings, update_tree_settings
  - file operations (list_files, get_diff, read_file with worktree-or-git fallback)

Written against the PLANNED interface from backend-rewrite-plan.md.
Will fail until the implementation is done.
"""

import pytest

from services.orchestrator import Orchestrator
from services.tree_service import (
    get_node,
    get_tree,
    update_node,
    set_setting,
    get_setting,
    update_tree,
)
from services.workspace_service import (
    _run_git,
    _GIT_ENV,
    ensure_worktree,
    resolve_workspace,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _init_project(project_path):
    """Initialise the project dir as a git repo with one commit."""
    await _run_git(project_path, "init")
    await _run_git(project_path, "config", "user.email", "test@test")
    await _run_git(project_path, "config", "user.name", "Test")
    gitignore = project_path / ".gitignore"
    gitignore.write_text(".codefission/\n.claude/\n_artifacts/\n")
    await _run_git(project_path, "add", "-A")
    await _run_git(project_path, "commit", "-m", "initial commit", env=_GIT_ENV)
    _, branch, _ = await _run_git(project_path, "rev-parse", "--abbrev-ref", "HEAD")
    return branch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def orch(tmp_db, tmp_project):
    return Orchestrator()


@pytest.fixture
def project(tmp_project):
    return tmp_project


# ---------------------------------------------------------------------------
# TestDeleteNode
# ---------------------------------------------------------------------------

class TestDeleteNode:
    """Orchestrator.delete_node — extracted from handlers.handle_delete_node."""

    @pytest.mark.asyncio
    async def test_deletes_leaf_node(self, orch, project):
        """Deleting a leaf node removes it from DB and updates parent's children."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)
        child = await orch.branch(root.id, label="leaf")

        # Verify child exists
        assert await get_node(child.id) is not None

        await orch.delete_node(child.id)

        # Node gone from DB
        assert await get_node(child.id) is None
        # Parent's children_ids updated
        parent = await get_node(root.id)
        assert child.id not in parent.children_ids

    @pytest.mark.asyncio
    async def test_deletes_subtree(self, orch, project):
        """Deleting a node also deletes all descendants."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)
        node_a = await orch.branch(root.id, label="A")
        node_b = await orch.branch(node_a.id, label="B")

        await orch.delete_node(node_a.id)

        assert await get_node(node_a.id) is None
        assert await get_node(node_b.id) is None

    @pytest.mark.asyncio
    async def test_cannot_delete_root(self, orch, project):
        """Attempting to delete the root node raises ValueError."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)

        with pytest.raises(ValueError):
            await orch.delete_node(root.id)

    @pytest.mark.asyncio
    async def test_cannot_delete_streaming_node(self, orch, project):
        """Cannot delete a node with status=active (currently streaming)."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        child = await orch.branch(root.id, label="streaming")
        await update_node(child.id, status="active")

        with pytest.raises(ValueError):
            await orch.delete_node(child.id)

    @pytest.mark.asyncio
    async def test_cleans_up_settings_on_delete(self, orch, project):
        """Expanded_nodes setting is cleaned up when a node is deleted."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)
        child = await orch.branch(root.id, label="tracked")

        # Simulate expanded_nodes containing the child ID
        import json
        await set_setting(f"expanded_nodes_{tree.id}", json.dumps([child.id, root.id]))

        await orch.delete_node(child.id)

        expanded_raw = await get_setting(f"expanded_nodes_{tree.id}")
        if expanded_raw:
            expanded = json.loads(expanded_raw)
            assert child.id not in expanded

    @pytest.mark.asyncio
    async def test_cleans_up_worktree_on_delete(self, orch, project):
        """Deleting a node removes its worktree directory."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)

        # Create a node with a worktree via prepare_chat
        ctx = await orch.prepare_chat(root.id, "make a file")
        workspace = ctx.workspace
        assert workspace.exists()

        # Complete the chat so node is in a deleteable state
        await orch.complete_chat(ctx.node_id, "done", "make a file", workspace)

        await orch.delete_node(ctx.node_id)

        # Worktree directory should be removed
        assert not workspace.exists()


# ---------------------------------------------------------------------------
# TestOpenRepo
# ---------------------------------------------------------------------------

class TestOpenRepo:
    """Orchestrator.open_repo — find or create tree for a repo."""

    @pytest.mark.asyncio
    async def test_finds_existing_tree(self, orch, project):
        """open_repo returns existing tree if repo_id + base_commit match."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree(
            "T", base_branch=branch,
            repo_id="repo1", repo_path=str(project),
        )

        result = await orch.open_repo(
            repo_id="repo1",
            repo_path=str(project),
            base_commit=tree.base_commit,
            base_branch=branch,
        )

        assert result.id == tree.id

    @pytest.mark.asyncio
    async def test_creates_new_tree_if_none_exists(self, orch, project):
        """open_repo creates a new tree when no match found."""
        branch = await _init_project(project)

        result = await orch.open_repo(
            repo_id="new-repo",
            repo_path=str(project),
            base_commit="abc123",
            base_branch=branch,
        )

        assert result is not None
        assert result.repo_id == "new-repo"

    @pytest.mark.asyncio
    async def test_updates_repo_path_if_moved(self, orch, project):
        """open_repo updates repo_path if the repo has moved."""
        branch = await _init_project(project)
        tree, _ = await orch.create_tree(
            "T", base_branch=branch,
            repo_id="repo1", repo_path="/old/path",
        )
        from services.tree_service import update_tree as _update_tree
        await _update_tree(tree.id, base_commit=tree.base_commit)

        result = await orch.open_repo(
            repo_id="repo1",
            repo_path="/new/path",
            base_commit=tree.base_commit,
            base_branch=branch,
        )

        fetched = await get_tree(result.id)
        assert fetched.repo_path == "/new/path"


# ---------------------------------------------------------------------------
# TestUpdateBase
# ---------------------------------------------------------------------------

class TestUpdateBase:
    """Orchestrator.update_base — update a tree's base commit."""

    @pytest.mark.asyncio
    async def test_updates_base_commit(self, orch, project):
        """update_base changes tree.base_commit to the new SHA."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)

        # Make a new commit
        (project / "new_file.py").write_text("# new\n")
        await _run_git(project, "add", "-A")
        await _run_git(project, "commit", "-m", "second commit", env=_GIT_ENV)
        _, new_sha, _ = await _run_git(project, "rev-parse", "HEAD")

        await orch.update_base(tree.id, new_sha)

        updated = await get_tree(tree.id)
        assert updated.base_commit == new_sha

    @pytest.mark.asyncio
    async def test_rejects_invalid_commit(self, orch, project):
        """update_base raises error for a nonexistent commit SHA."""
        branch = await _init_project(project)
        tree, _ = await orch.create_tree("T", base_branch=branch)

        with pytest.raises((ValueError, RuntimeError)):
            await orch.update_base(tree.id, "deadbeef" * 5)

    @pytest.mark.asyncio
    async def test_rejects_update_after_children_created(self, orch, project):
        """update_base raises error if root has children (conversations exist)."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)
        await orch.branch(root.id, label="child")

        # Make a new commit
        (project / "new_file.py").write_text("# new\n")
        await _run_git(project, "add", "-A")
        await _run_git(project, "commit", "-m", "second commit", env=_GIT_ENV)
        _, new_sha, _ = await _run_git(project, "rev-parse", "HEAD")

        with pytest.raises((ValueError, RuntimeError)):
            await orch.update_base(tree.id, new_sha)


# ---------------------------------------------------------------------------
# TestSettings
# ---------------------------------------------------------------------------

class TestSettings:
    """Orchestrator settings — global and tree-level."""

    @pytest.mark.asyncio
    async def test_update_global_settings(self, orch, project):
        """update_global_settings persists provider, model, max_turns."""
        branch = await _init_project(project)

        await orch.update_global_settings(
            default_provider="codex",
            default_model="o4-mini",
            default_max_turns="10",
        )

        from services.tree_service import get_global_defaults
        defaults = await get_global_defaults()
        assert defaults["provider"] == "codex"
        assert defaults["model"] == "o4-mini"
        assert defaults["max_turns"] == 10

    @pytest.mark.asyncio
    async def test_update_tree_settings(self, orch, project):
        """update_tree_settings persists tree-level overrides."""
        branch = await _init_project(project)
        tree, _ = await orch.create_tree("T", base_branch=branch)

        await orch.update_tree_settings(tree.id, provider="codex", model="o4-mini")

        updated = await get_tree(tree.id)
        assert updated.provider == "codex"
        assert updated.model == "o4-mini"

    @pytest.mark.asyncio
    async def test_tree_settings_override_global(self, orch, project):
        """Tree-level model overrides global default model."""
        branch = await _init_project(project)
        await set_setting("default_model", "claude-opus-4-6")

        tree, _ = await orch.create_tree("T", base_branch=branch)
        await update_tree(tree.id, model="claude-sonnet-4-6")

        from services.tree_service import resolve_tree_settings
        fetched_tree = await get_tree(tree.id)
        effective = await resolve_tree_settings(fetched_tree)
        assert effective["model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# TestFileOperations
# ---------------------------------------------------------------------------

class TestFileOperations:
    """File operations: list_files, get_diff, read_file — with worktree-or-git fallback."""

    @pytest.mark.asyncio
    async def test_list_files_from_worktree(self, orch, project):
        """list_files returns files when worktree exists."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "create files")
        (ctx.workspace / "hello.py").write_text("print('hello')\n")
        await _run_git(ctx.workspace, "add", "-A")
        await _run_git(ctx.workspace, "commit", "-m", "add file", env=_GIT_ENV)

        files = await orch.list_files(ctx.node_id)
        assert any("hello.py" in f for f in files)

    @pytest.mark.asyncio
    async def test_list_files_from_commit_when_no_worktree(self, orch, project):
        """list_files falls back to git commit when worktree is gone."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "create file")
        (ctx.workspace / "from_commit.py").write_text("# from commit\n")

        result = await orch.complete_chat(
            ctx.node_id, "Created file", "create file", ctx.workspace,
        )

        # Remove the worktree to force fallback
        import shutil
        if ctx.workspace.exists():
            shutil.rmtree(ctx.workspace)

        files = await orch.list_files(ctx.node_id)
        # Should still find the file via git commit
        assert any("from_commit.py" in f for f in files)

    @pytest.mark.asyncio
    async def test_get_diff_shows_changes(self, orch, project):
        """get_diff returns unified diff of changes in a node."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "add file")
        (ctx.workspace / "diff_test.py").write_text("# new file\n")
        await _run_git(ctx.workspace, "add", "-A")
        await _run_git(ctx.workspace, "commit", "-m", "add diff_test", env=_GIT_ENV)

        diff = await orch.get_diff(ctx.node_id)
        assert "diff_test.py" in diff

    @pytest.mark.asyncio
    async def test_read_file_content(self, orch, project):
        """read_file returns file content from worktree."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "create readme")
        (ctx.workspace / "readme.txt").write_text("Hello World\n")

        content = await orch.read_file(ctx.node_id, "readme.txt")
        assert "Hello World" in content
