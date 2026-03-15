"""Tests for planting a new tree from an existing node.

Validates that create_tree(from_node_id=...) inherits git_commit, repo context,
and instructions (skill) from the source node/tree.
"""

import pytest

from orchestrator import Orchestrator
from store.trees import get_tree, get_node, update_node, update_tree
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


class TestPlantFromNode:

    @pytest.mark.asyncio
    async def test_plant_from_node_inherits_commit(self, orch, project):
        """Planting from a node uses that node's git_commit as the new tree's base."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree(
            "Source Tree", base_branch=branch,
            repo_id="repo1", repo_path=str(project), repo_name="proj",
        )

        # Simulate a chat that produces a commit — prepare + complete
        ctx = await orch.prepare_chat(root.id, "Write hello.py")
        (ctx.workspace / "hello.py").write_text("print('hello')\n")
        result = await orch.complete_chat(
            ctx.node_id, "Created hello.py", "Write hello.py", ctx.workspace,
        )
        assert result.git_commit is not None

        # Plant new tree from that node
        new_tree, new_root = await orch.create_tree(
            "Planted Tree", base_branch=branch,
            repo_id="repo1", repo_path=str(project), repo_name="proj",
            from_node_id=ctx.node_id,
        )

        # New root should have the source node's git_commit
        assert new_root.git_commit == result.git_commit
        assert new_tree.base_commit == result.git_commit

    @pytest.mark.asyncio
    async def test_plant_from_node_inherits_repo(self, orch, project):
        """Planted tree inherits repo_id, repo_path from parameters."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree(
            "Source", base_branch=branch,
            repo_id="repo-abc", repo_path=str(project), repo_name="my-repo",
        )

        new_tree, new_root = await orch.create_tree(
            "Planted", base_branch=branch,
            repo_id="repo-abc", repo_path=str(project), repo_name="my-repo",
            from_node_id=root.id,
        )

        fetched = await get_tree(new_tree.id)
        assert fetched.repo_id == "repo-abc"
        assert fetched.repo_path == str(project)
        assert fetched.repo_name == "my-repo"

    @pytest.mark.asyncio
    async def test_plant_from_node_inherits_instructions(self, orch, project):
        """Planted tree inherits the source tree's skill (instructions)."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree(
            "Skilled Tree", base_branch=branch,
            repo_id="repo1", repo_path=str(project), repo_name="proj",
        )
        # Set a skill on the source tree
        await update_tree(tree.id, skill="Be concise")

        new_tree, new_root = await orch.create_tree(
            "Planted", base_branch=branch,
            repo_id="repo1", repo_path=str(project), repo_name="proj",
            from_node_id=root.id,
        )

        fetched = await get_tree(new_tree.id)
        assert fetched.skill == "Be concise"

    @pytest.mark.asyncio
    async def test_plant_from_node_inherits_repo_from_source_tree(self, orch, project):
        """When repo_id/repo_path are not explicitly passed, inherit from source tree."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree(
            "Source", base_branch=branch,
            repo_id="repo-xyz", repo_path=str(project), repo_name="source-repo",
        )

        # Plant without passing repo_id/repo_path — should inherit from source tree
        new_tree, new_root = await orch.create_tree(
            "Planted", base_branch=branch,
            from_node_id=root.id,
        )

        fetched = await get_tree(new_tree.id)
        assert fetched.repo_id == "repo-xyz"
        assert fetched.repo_path == str(project)
        assert fetched.repo_name == "source-repo"

    @pytest.mark.asyncio
    async def test_plant_from_nonexistent_node_raises(self, orch, project):
        """Planting from a nonexistent node raises ValueError."""
        branch = await _init_project(project)

        with pytest.raises(ValueError, match="not found"):
            await orch.create_tree(
                "Bad Plant", base_branch=branch,
                from_node_id="nonexistent",
            )
