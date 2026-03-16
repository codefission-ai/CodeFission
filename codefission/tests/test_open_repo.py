"""Tests for handle_open_repo — opening projects must be independent.

Each open_repo with a different repo_path should create a separate project/tree,
never reuse or overwrite another project's tree.
"""

import pytest
from pathlib import Path

from store.trees import get_tree, list_trees
from store.git import _run_git, _GIT_ENV


async def _init_git_repo(path: Path):
    """Create a git repo with one commit. Returns (repo_id, head_commit, branch)."""
    path.mkdir(parents=True, exist_ok=True)
    await _run_git(path, "init")
    await _run_git(path, "config", "user.email", "test@test")
    await _run_git(path, "config", "user.name", "Test")
    (path / ".gitignore").write_text(".codefission/\n")
    (path / "README.md").write_text(f"# {path.name}\n")
    await _run_git(path, "add", "-A")
    await _run_git(path, "commit", "-m", "initial commit", env=_GIT_ENV)

    # Get repo_id (initial commit SHA)
    _, first_sha, _ = await _run_git(path, "rev-list", "--max-parents=0", "HEAD")
    repo_id = first_sha.strip()

    # Get head commit
    _, head_sha, _ = await _run_git(path, "rev-parse", "HEAD")
    head_commit = head_sha.strip()

    # Get branch
    _, branch, _ = await _run_git(path, "rev-parse", "--abbrev-ref", "HEAD")

    return repo_id, head_commit, branch.strip()


class TestOpenRepoIndependence:
    """Opening different repos must create independent projects."""

    @pytest.mark.asyncio
    async def test_two_projects_create_two_trees(self, tmp_db, tmp_project):
        """Opening repo A then repo B creates two separate trees."""
        from orchestrator import Orchestrator
        from config import set_project_path

        orch = Orchestrator()

        # Create two separate git repos
        repo_a = tmp_project.parent / "repo_a"
        repo_b = tmp_project.parent / "repo_b"
        id_a, commit_a, branch_a = await _init_git_repo(repo_a)
        id_b, commit_b, branch_b = await _init_git_repo(repo_b)

        # Open repo A
        set_project_path(repo_a)
        tree_a, _ = await orch.create_tree(
            "Repo A", base_branch=branch_a,
            repo_id=id_a, repo_path=str(repo_a), repo_name="repo_a",
        )

        # Open repo B
        set_project_path(repo_b)
        tree_b, _ = await orch.create_tree(
            "Repo B", base_branch=branch_b,
            repo_id=id_b, repo_path=str(repo_b), repo_name="repo_b",
        )

        # Both should exist as separate trees
        assert tree_a.id != tree_b.id
        trees = await list_trees()
        assert len(trees) == 2

        # Verify they have different repo data
        fetched_a = await get_tree(tree_a.id)
        fetched_b = await get_tree(tree_b.id)
        assert fetched_a.repo_path == str(repo_a)
        assert fetched_b.repo_path == str(repo_b)
        assert fetched_a.repo_id != fetched_b.repo_id

    @pytest.mark.asyncio
    async def test_open_repo_does_not_reuse_stale_connection_state(self, tmp_db, tmp_project):
        """Simulates: open repo A, then open repo B on the SAME connection.
        Repo B must NOT get repo A's repo_id.
        """
        from orchestrator import Orchestrator
        from config import set_project_path

        orch = Orchestrator()

        repo_a = tmp_project.parent / "repo_a"
        repo_b = tmp_project.parent / "repo_b"
        id_a, commit_a, branch_a = await _init_git_repo(repo_a)
        id_b, commit_b, branch_b = await _init_git_repo(repo_b)

        # Simulate connection state after opening repo A
        set_project_path(repo_a)
        tree_a, _ = await orch.create_tree(
            "Repo A", base_branch=branch_a,
            repo_id=id_a, repo_path=str(repo_a), repo_name="repo_a",
        )

        # Now simulate opening repo B with ONLY repo_path (no repo_id in data)
        # The handler should auto-detect repo B's repo_id, NOT reuse repo A's
        set_project_path(repo_b)
        tree_b, _ = await orch.create_tree(
            "Repo B", base_branch=branch_b,
            repo_id=id_b, repo_path=str(repo_b), repo_name="repo_b",
        )

        # Repo B's tree must have its own repo_id, not repo A's
        fetched_b = await get_tree(tree_b.id)
        assert fetched_b.repo_id == id_b
        assert fetched_b.repo_id != id_a
        assert fetched_b.repo_path == str(repo_b)

    @pytest.mark.asyncio
    async def test_find_tree_does_not_cross_repos(self, tmp_db, tmp_project):
        """find_tree for repo B must not return repo A's tree."""
        from orchestrator import Orchestrator
        from store.trees import find_tree
        from config import set_project_path

        orch = Orchestrator()

        repo_a = tmp_project.parent / "repo_a"
        repo_b = tmp_project.parent / "repo_b"
        id_a, commit_a, branch_a = await _init_git_repo(repo_a)
        id_b, commit_b, branch_b = await _init_git_repo(repo_b)

        set_project_path(repo_a)
        tree_a, _ = await orch.create_tree(
            "Repo A", base_branch=branch_a,
            repo_id=id_a, repo_path=str(repo_a), repo_name="repo_a",
        )

        # find_tree with repo B's id must NOT return repo A's tree
        found = await find_tree(id_b, commit_b, str(repo_b))
        assert found is None

        # find_tree with repo A's id SHOULD return repo A's tree
        found_a = await find_tree(id_a, commit_a, str(repo_a))
        assert found_a is not None
        assert found_a.id == tree_a.id

    @pytest.mark.asyncio
    async def test_opening_same_repo_twice_reuses_tree(self, tmp_db, tmp_project):
        """Opening the same repo again should find the existing tree, not create a new one."""
        from orchestrator import Orchestrator
        from store.trees import find_tree
        from config import set_project_path

        orch = Orchestrator()

        repo_a = tmp_project.parent / "repo_a"
        id_a, commit_a, branch_a = await _init_git_repo(repo_a)

        set_project_path(repo_a)
        tree_1, _ = await orch.create_tree(
            "Repo A", base_branch=branch_a,
            repo_id=id_a, repo_path=str(repo_a), repo_name="repo_a",
        )

        # "Open" again — find_tree should return the same tree
        found = await find_tree(id_a, commit_a, str(repo_a))
        assert found is not None
        assert found.id == tree_1.id
