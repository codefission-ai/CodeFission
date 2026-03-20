"""Tests for planted-tree runtime bugs.

After the plant-tree bugs (Bug A / Bug B) were fixed, one more runtime
problem was found:

────────────────────────────────────────────────────────────────────────
Bug C — Planted-tree root shows main repo files, not planted-commit files
────────────────────────────────────────────────────────────────────────

Scenario: user chats on a node → agent writes hello.py → commit ends up
on ct-{node_id} branch.  User plants a new tree from that node.
New tree root has base_commit = ct-sha (the commit that contains hello.py).

Expected: orch.list_node_files(new_root_id)  →  includes "hello.py"
Actual:   list_node_files resolves the root's workspace via
              resolve_workspace(root_id, root_id)  →  project_path
          project_path.exists() is True (it's the live repo dir).
          list_files(project_path) reads the working directory.
          hello.py only exists on the ct- branch, not in main.
          Result: hello.py is absent.

Root cause (orchestrator/files.py): for root nodes, ws_path == project_path
(always exists), so list_files_from_commit(node.git_commit) is never reached.
"""

import pytest
from unittest.mock import patch

from agentbridge import SessionInit, TextDelta, TurnComplete
from orchestrator import Orchestrator
from store.trees import get_node, get_tree
from store.git import _run_git, _GIT_ENV


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _init_project(project_path):
    """Initialise project dir as a git repo with one commit."""
    await _run_git(project_path, "init")
    await _run_git(project_path, "config", "user.email", "test@test")
    await _run_git(project_path, "config", "user.name", "Test")
    gitignore = project_path / ".gitignore"
    gitignore.write_text(".codefission/\n.claude/\n_artifacts/\n")
    await _run_git(project_path, "add", "-A")
    await _run_git(project_path, "commit", "-m", "initial commit", env=_GIT_ENV)
    _, branch, _ = await _run_git(project_path, "rev-parse", "--abbrev-ref", "HEAD")
    return branch.strip()


def _make_mock_stream(session_id="test-session", text="ok"):
    async def mock_stream(*args, **kwargs):
        yield SessionInit(session_id=session_id, provider="claude")
        yield TextDelta(text=text, provider="claude")
        yield TurnComplete(session_id=session_id, cost_usd=0.01, provider="claude")
    return mock_stream


async def _run_chat(orch, parent_id, message, session_id="session-abc", write_file=None, project=None):
    """Run a mocked chat; optionally write a file so auto_commit sees changes."""
    node_id = None

    async def _streaming_side_effect(*args, **kwargs):
        if write_file and project:
            from pathlib import Path
            workspace = args[2] if len(args) > 2 else kwargs.get("workspace")
            if workspace:
                ws = Path(workspace) if not isinstance(workspace, Path) else workspace
                ws.mkdir(parents=True, exist_ok=True)
                (ws / write_file).write_text("# generated\n")
        async for ev in _make_mock_stream(session_id)(*args, **kwargs):
            yield ev

    with patch("orchestrator.chat.stream_chat", side_effect=_streaming_side_effect):
        async for event in orch.chat(parent_id, message):
            if type(event).__name__ == "ChatNodeCreated":
                node_id = event.node.id

    return await get_node(node_id)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def orch(tmp_db, tmp_project):
    return Orchestrator()


@pytest.fixture
def project(tmp_project):
    return tmp_project


# ── Bug C: planted root shows wrong files ────────────────────────────────────


class TestPlantedRootFiles:
    """Planted-tree root's file list reflects planted commit, not main repo."""

    @pytest.mark.asyncio
    async def test_planted_root_shows_file_from_ct_commit(self, orch, project):
        """After planting from a chat node that created hello.py, the new
        tree's root node should list hello.py in its files.

        BUG: list_node_files resolves root → project_path (always exists) →
        list_files(project_path) reads main working directory → hello.py not
        there (it's only on the ct- branch) → file absent from list.
        """
        git_branch = await _init_project(project)
        source_tree, root = await orch.create_tree("Source", base_branch=git_branch)

        # Chat writes hello.py into the worktree → auto_commit records it on
        # a ct-{node_id} branch.
        chat_node = await _run_chat(
            orch, root.id, "write hello",
            session_id="s1",
            write_file="hello.py",
            project=project,
        )

        assert chat_node.git_commit is not None, "Precondition: chat_node needs a commit"

        # Plant new tree from that node
        new_tree, new_root = await orch.create_tree("Planted", from_node_id=chat_node.id)
        assert new_tree.base_commit == chat_node.git_commit, "Precondition: new tree roots at ct-commit"

        # The planted root's file list must include hello.py
        files = await orch.list_node_files(new_root.id)
        assert "hello.py" in files.files, (
            f"hello.py is in the planted commit ({chat_node.git_commit[:7]}) but "
            f"missing from list_node_files(new_root). Got: {files.files}. "
            "Root cause: resolve_workspace(root_id, root_id) → project_path, "
            "list_files(project_path) reads main working directory where "
            "hello.py does not exist."
        )

    @pytest.mark.asyncio
    async def test_child_workspace_does_have_file(self, orch, project):
        """Contrast: the chat node that created hello.py DOES show it in its
        own file list (because the child worktree path exists).
        This isolates the bug to the root node case only.
        """
        git_branch = await _init_project(project)
        _, root = await orch.create_tree("Source", base_branch=git_branch)

        chat_node = await _run_chat(
            orch, root.id, "write hello",
            session_id="s1",
            write_file="hello.py",
            project=project,
        )

        # The chat node itself (a child) should list hello.py
        files = await orch.list_node_files(chat_node.id)
        assert "hello.py" in files.files, (
            f"Expected hello.py in child node files, got: {files.files}"
        )

    @pytest.mark.asyncio
    async def test_planted_root_does_not_show_main_only_files(self, orch, project):
        """Files that exist only in main (not in the planted commit) should
        NOT appear in the planted root's file list.

        We add readme.txt to main AFTER planting, then check that the
        planted root does not see readme.txt (since it was not in the commit
        it is based on).

        BUG: since list_files(project_path) reads the live working dir, any
        file added to main after planting appears falsely in the planted root.
        """
        git_branch = await _init_project(project)
        _, root = await orch.create_tree("Source", base_branch=git_branch)

        chat_node = await _run_chat(
            orch, root.id, "write hello",
            session_id="s1",
            write_file="hello.py",
            project=project,
        )

        new_tree, new_root = await orch.create_tree("Planted", from_node_id=chat_node.id)

        # Add a new file to main AFTER planting
        (project / "readme.txt").write_text("main only\n")
        await _run_git(project, "add", "readme.txt")
        await _run_git(project, "commit", "-m", "add readme", env=_GIT_ENV)

        files = await orch.list_node_files(new_root.id)
        assert "readme.txt" not in files.files, (
            "readme.txt was added to main after planting and should NOT appear "
            f"in the planted root's file list, but got: {files.files}. "
            "Bug: list_files(project_path) reads the live working directory."
        )

