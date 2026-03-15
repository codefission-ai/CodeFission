"""Tests for sidebar tree creation — creating trees with repo context."""

import pytest

from orchestrator import Orchestrator
from store.trees import get_tree, get_node, create_tree as _store_create_tree
from store.git import _run_git, _GIT_ENV


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


@pytest.fixture
def orch(tmp_db, tmp_project):
    return Orchestrator()


@pytest.fixture
def project(tmp_project):
    return tmp_project


class TestTreeCreation:

    @pytest.mark.asyncio
    async def test_create_tree_with_repo_context(self, orch, project):
        """Creating a tree with repo_id and repo_path sets them on the tree."""
        branch = await _init_project(project)

        tree, root = await orch.create_tree(
            "My Tree",
            base_branch=branch,
            repo_id="repo123",
            repo_path=str(project),
            repo_name="my-project",
        )

        assert tree.repo_id == "repo123"
        assert tree.repo_path == str(project)
        assert tree.repo_name == "my-project"

        # Verify persisted in DB
        fetched = await get_tree(tree.id)
        assert fetched.repo_id == "repo123"
        assert fetched.repo_path == str(project)
        assert fetched.repo_name == "my-project"

    @pytest.mark.asyncio
    async def test_create_tree_without_repo_context_still_works(self, orch, project):
        """Creating a tree without repo_path still succeeds (uses project context)."""
        branch = await _init_project(project)

        tree, root = await orch.create_tree(
            "No Repo Context",
            base_branch=branch,
        )

        # Tree is created with no repo info
        assert tree.repo_id is None
        assert tree.repo_path is None
        assert root.tree_id == tree.id
        assert root.parent_id is None
