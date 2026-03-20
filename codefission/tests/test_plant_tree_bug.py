"""Tests demonstrating the 'Plant new tree' bug in both entry points.

Two separate bugs, same symptom ('Plant new tree' doesn't work):

────────────────────────────────────────────────────────────────────────
Bug A — Git graph: base_commit is silently dropped
────────────────────────────────────────────────────────────────────────

When the user clicks a historical commit in the git graph and hits
"Plant new tree here", the frontend sends:

    { type: CREATE_TREE, base_branch: "main", base_commit: "<sha>", ... }

The handler (handle_create_tree) reads base_branch and from_node_id, but
NEVER reads base_commit from the message.  orch.create_tree also has no
base_commit parameter for the non-from_node_id path; it always resolves
HEAD of base_branch via git rev-parse.

Result: the tree is planted at the current HEAD of "main", not at the
historical commit the user selected.

────────────────────────────────────────────────────────────────────────
Bug B — From a node: base_branch is set to an internal ct- branch name
────────────────────────────────────────────────────────────────────────

When the user clicks "Plant new tree" on a chat node, the frontend sends:

    { type: CREATE_TREE, from_node_id: "<id>", repo_id: ..., repo_path: ... }

Inside create_tree (orchestrator/trees.py):

    head_sha      = source_node.git_commit        ← correct
    actual_branch = source_node.git_branch or base_branch

After the git_branch tracking fix, source_node.git_branch is "ct-{nid}"
for any node that changed files.  So:

    actual_branch = "ct-{nid}"    ← CodeFission-internal branch name

The new tree is created with base_branch = "ct-{nid}", which:
  • Shows a confusing internal name in the UI branch selector
  • Is filtered OUT of list_branches, so the dropdown has a dangling option
  • Is semantically wrong — the tree should track the same base_branch as
    the source tree (e.g. "main")

All tests below are expected to FAIL on the current buggy code.
"""

import pytest
from unittest.mock import patch

from agentbridge import SessionInit, TextDelta, TurnComplete
from orchestrator import Orchestrator
from store.trees import get_node, get_tree, update_node
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


async def _head_sha(project_path):
    _, sha, _ = await _run_git(project_path, "rev-parse", "HEAD")
    return sha.strip()


def _make_mock_stream(session_id="test-session", text="ok"):
    async def mock_stream(*args, **kwargs):
        yield SessionInit(session_id=session_id, provider="claude")
        yield TextDelta(text=text, provider="claude")
        yield TurnComplete(session_id=session_id, cost_usd=0.01, provider="claude")
    return mock_stream


async def _run_chat(orch, parent_id, message, session_id="session-abc", write_file=None, project=None):
    """Run a mocked chat; optionally write a file inside the worktree so that
    auto_commit records real file changes (making files_changed > 0 so that
    post_chat_cleanup keeps the branch and git_branch is not reset to None)."""
    node_id = None

    async def _streaming_side_effect(*args, **kwargs):
        # Write a file into the workspace before streaming so auto_commit
        # sees a real change and sets files_changed > 0.
        # stream_chat signature: (node_id, message, workspace, parent_session_id, ...)
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


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def orch(tmp_db, tmp_project):
    return Orchestrator()


@pytest.fixture
def project(tmp_project):
    return tmp_project


# ── Bug A: git graph drops base_commit ───────────────────────────────────────


class TestGitGraphPlantTreeBug:
    """The git graph sends base_commit=<sha> but create_tree ignores it."""

    @pytest.mark.asyncio
    async def test_create_tree_plants_at_historical_commit(self, orch, project):
        """orch.create_tree now accepts base_commit and plants the tree at
        the specified historical commit rather than always resolving HEAD.
        """
        git_branch = await _init_project(project)
        initial_sha = await _head_sha(project)

        # Make a second commit so HEAD moves forward
        (project / "v2.py").write_text("x = 2\n")
        await _run_git(project, "add", "v2.py")
        await _run_git(project, "commit", "-m", "second commit", env=_GIT_ENV)
        head_sha = await _head_sha(project)

        assert initial_sha != head_sha, "Setup: two distinct commits required"

        # Plant at the historical commit — git graph passes base_commit
        tree, _ = await orch.create_tree("T", base_branch=git_branch, base_commit=initial_sha)

        assert tree.base_commit == initial_sha, (
            f"Expected tree at historical commit ({initial_sha[:7]}) "
            f"but got {tree.base_commit[:7]}"
        )
        # base_branch is still the tracking branch, not the commit
        assert tree.base_branch == git_branch

    @pytest.mark.asyncio
    async def test_handle_create_tree_passes_base_commit(self, orch, project):
        """handle_create_tree now reads base_commit from the WS message and
        passes it to orch.create_tree, so the git graph's selected commit
        is honoured.

        Simulates the full handler call path (minus the WS layer).
        """
        git_branch = await _init_project(project)
        initial_sha = await _head_sha(project)

        # Advance HEAD past initial_sha
        (project / "v2.py").write_text("x = 2\n")
        await _run_git(project, "add", "v2.py")
        await _run_git(project, "commit", "-m", "second commit", env=_GIT_ENV)
        head_sha = await _head_sha(project)

        # Simulate what handle_create_tree now does:
        #   base_commit = data.get("base_commit")  ← now read from message
        #   orch.create_tree(..., base_commit=base_commit)
        tree, _ = await orch.create_tree(
            "T",
            base_branch=git_branch,
            from_node_id=None,
            base_commit=initial_sha,   # ← forwarded from WS message
        )

        assert tree.base_commit == initial_sha, (
            f"Tree should land at the git-graph-selected commit ({initial_sha[:7]}) "
            f"not HEAD ({head_sha[:7]})"
        )

    @pytest.mark.asyncio
    async def test_create_tree_without_base_commit_still_uses_head(self, orch, project):
        """Without base_commit the normal path still resolves HEAD of base_branch
        (no regression for the standard 'create new tree' flow).
        """
        git_branch = await _init_project(project)
        head_sha = await _head_sha(project)

        tree, _ = await orch.create_tree("T", base_branch=git_branch)

        assert tree.base_commit == head_sha


# ── Bug B: from-node uses wrong base_branch ──────────────────────────────────


class TestFromNodePlantTreeBug:
    """Planting from a node gives the new tree the wrong base_branch."""

    @pytest.mark.asyncio
    async def test_plant_from_node_base_branch_is_ct_branch(self, orch, project):
        """When planting from a chat node that changed files, the new tree's
        base_branch is the internal CodeFission branch "ct-{node.id}" instead
        of the source tree's base_branch (e.g. "main").

        Root cause in create_tree (orchestrator/trees.py):
            actual_branch = source_node.git_branch or base_branch
        After the git_branch tracking fix, source_node.git_branch = "ct-{nid}",
        so actual_branch = "ct-{nid}" and the new tree inherits that as its
        base_branch.

        FAILS: new_tree.base_branch is "ct-{...}" instead of source tree's branch.
        """
        git_branch = await _init_project(project)
        source_tree, root = await orch.create_tree("Source", base_branch=git_branch)

        # Run a chat that writes a file so post_chat_cleanup keeps the branch
        # (files_changed > 0) and git_branch is not reset to None.
        chat_node = await _run_chat(
            orch, root.id, "write something",
            session_id="s1",
            write_file="output.py",
            project=project,
        )

        # After the git_branch fix, chat nodes that changed files have
        # git_branch = "ct-{node.id}" (an internal CodeFission branch).
        assert chat_node.git_branch is not None, (
            "Precondition failed: chat node should have git_branch set "
            "after the git_branch tracking fix"
        )
        assert chat_node.git_branch.startswith("ct-"), (
            f"Precondition: git_branch should be an internal ct- name, "
            f"got {chat_node.git_branch!r}"
        )

        # Plant a new tree from this node
        new_tree, _ = await orch.create_tree("New", from_node_id=chat_node.id)

        # BUG: base_branch is the internal ct- branch name
        assert new_tree.base_branch == git_branch, (
            f"New tree's base_branch should be the source tree's base_branch "
            f"'{git_branch}', but got '{new_tree.base_branch}'. "
            f"create_tree sets actual_branch = source_node.git_branch = "
            f"'{chat_node.git_branch}' (an internal branch) instead of "
            f"source_tree.base_branch = '{git_branch}'."
        )

    @pytest.mark.asyncio
    async def test_plant_from_node_base_commit_is_correct(self, orch, project):
        """Contrast test: the base_commit IS correctly set when planting from
        a node — only base_branch is wrong.  This test should PASS to show
        the bug is isolated to base_branch, not base_commit.
        """
        git_branch = await _init_project(project)
        _, root = await orch.create_tree("Source", base_branch=git_branch)

        chat_node = await _run_chat(
            orch, root.id, "write something",
            session_id="s1",
            write_file="output.py",
            project=project,
        )

        new_tree, _ = await orch.create_tree("New", from_node_id=chat_node.id)

        # base_commit IS the node's commit — this part is correct.
        assert new_tree.base_commit == chat_node.git_commit, (
            "base_commit should equal the source node's git_commit"
        )

    @pytest.mark.asyncio
    async def test_plant_from_node_ct_branch_missing_from_branch_list(self, orch, project):
        """When the new tree has base_branch = "ct-{nid}", that branch is
        deliberately filtered out by list_branches (which skips ct- prefixes).
        So the branch selector in the UI will have the ct- name as an isolated
        option that cannot be selected from the normal list.

        FAILS: new_tree.base_branch should appear in list_branches() output.
        """
        from store.git import list_branches

        git_branch = await _init_project(project)
        _, root = await orch.create_tree("Source", base_branch=git_branch)

        chat_node = await _run_chat(
            orch, root.id, "write something",
            session_id="s1",
            write_file="output.py",
            project=project,
        )

        new_tree, _ = await orch.create_tree("New", from_node_id=chat_node.id)

        # The base_branch of the new tree should appear in the branch list so
        # the UI can display and update it correctly.
        branches = await list_branches()
        branch_names = [b["name"] for b in branches]

        assert new_tree.base_branch in branch_names, (
            f"new_tree.base_branch='{new_tree.base_branch}' is not in "
            f"list_branches() output {branch_names}. "
            f"ct- branches are filtered by list_branches, so the UI branch "
            f"selector can't display or change the base_branch of a tree "
            f"planted from a node with code changes."
        )
