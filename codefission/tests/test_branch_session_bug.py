"""Tests that catch the branch-node session continuity bug.

Bug: orchestrator.branch() creates a node with session_id=None even when the
parent has a session_id from a completed chat. This severs the conversation
context chain — a child of the branch node starts with a completely fresh AI
session, receiving neither the native session fork nor the ancestor text-preamble
fallback (which is also gated behind the same None check in stream_chat).

All tests in this class are expected to FAIL on the current buggy code and
PASS once the bug is fixed.
"""

import pytest
from unittest.mock import patch, AsyncMock

from agentbridge import SessionInit, TextDelta, TurnComplete
from orchestrator import Orchestrator
from store.trees import get_node, update_node
from store.git import _run_git, _GIT_ENV


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    return branch


def _make_mock_stream(session_id="test-session", text="response"):
    """Return a canned async generator for stream_chat."""
    async def mock_stream(*args, **kwargs):
        yield SessionInit(session_id=session_id, provider="claude")
        yield TextDelta(text=text, provider="claude")
        yield TurnComplete(session_id=session_id, cost_usd=0.01, provider="claude")
    return mock_stream


async def _run_chat(orch, parent_id, message, session_id="session-abc"):
    """Run a mocked chat and stamp a session_id on the resulting node.

    Returns the completed child node.
    """
    with patch("orchestrator.chat.stream_chat", side_effect=_make_mock_stream(session_id)):
        child_id = None
        async for event in orch.chat(parent_id, message):
            if type(event).__name__ == "ChatNodeCreated":
                child_id = event.node.id
    # stamp a deterministic session_id so tests don't depend on SDK internals
    await update_node(child_id, session_id=session_id)
    return await get_node(child_id)


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
# Tests
# ---------------------------------------------------------------------------

class TestBranchSessionBug:

    @pytest.mark.asyncio
    async def test_branch_inherits_session_id_from_parent(self, orch, project):
        """branch() must copy parent.session_id to the new node.

        Currently FAILS: branch() copies only git_commit; session_id is left
        as None, silently severing the conversation context chain.
        """
        git_branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=git_branch)

        parent = await _run_chat(orch, root.id, "first question", session_id="session-parent-1")
        assert parent.session_id == "session-parent-1"

        branch_node = await orch.branch(parent.id)

        assert branch_node.session_id == parent.session_id, (
            f"branch() dropped session_id: got {branch_node.session_id!r}, "
            f"expected {parent.session_id!r}"
        )

    @pytest.mark.asyncio
    async def test_branch_child_prepare_chat_has_parent_session(self, orch, project):
        """ChatContext for child of branch node must carry parent_session_id.

        Currently FAILS: branch_node.session_id=None propagates into
        prepare_chat, so parent_session_id=None is returned in the context —
        the session fork and ancestor fallback are both skipped downstream.
        """
        git_branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=git_branch)

        parent = await _run_chat(orch, root.id, "first question", session_id="session-parent-2")
        branch_node = await orch.branch(parent.id)

        ctx = await orch.prepare_chat(branch_node.id, "follow-up in branch")

        assert ctx.parent_session_id is not None, (
            "prepare_chat returned parent_session_id=None for child of branch node — "
            "both session fork and ancestor context fallback will be bypassed"
        )

    @pytest.mark.asyncio
    async def test_stream_chat_receives_session_for_branch_child(self, orch, project):
        """stream_chat must be called with a non-None parent_session_id.

        Currently FAILS: the None propagates all the way to store/ai.py where
        the `if parent_session_id:` guard skips resolve_session_continuity
        entirely — no fork, no ancestor text preamble.
        """
        git_branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=git_branch)

        parent = await _run_chat(orch, root.id, "first question", session_id="session-parent-3")
        branch_node = await orch.branch(parent.id)

        captured = {}

        async def capturing_stream(*args, **kwargs):
            # parent_session_id is the 4th positional arg in stream_chat's signature:
            # stream_chat(node_id, user_message, workspace, parent_session_id, *, ...)
            captured["parent_session_id"] = args[3] if len(args) > 3 else kwargs.get("parent_session_id")
            yield SessionInit(session_id="new-session", provider="claude")
            yield TextDelta(text="response", provider="claude")
            yield TurnComplete(session_id="new-session", cost_usd=0.01, provider="claude")

        with patch("orchestrator.chat.stream_chat", side_effect=capturing_stream):
            async for _ in orch.chat(branch_node.id, "follow-up in branch"):
                pass

        assert captured.get("parent_session_id") is not None, (
            "stream_chat received parent_session_id=None — both session fork and "
            "ancestor context fallback are bypassed for children of branch nodes"
        )

    @pytest.mark.asyncio
    async def test_ancestor_context_fallback_fires_for_branch_child(self, orch, project):
        """resolve_session_continuity must be called for children of branch nodes.

        Currently FAILS: the `if parent_session_id:` guard in stream_chat skips
        resolve_session_continuity when parent_session_id=None, so even the
        text-preamble ancestor fallback (the cross-provider recovery path) is
        never triggered.
        """
        git_branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=git_branch)

        parent = await _run_chat(orch, root.id, "first question", session_id="session-parent-4")
        branch_node = await orch.branch(parent.id)

        with patch("store.ai.resolve_session_continuity", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = (None, False, "[System: prior context]")

            async def mock_stream(*args, **kwargs):
                yield SessionInit(session_id="s", provider="claude")
                yield TextDelta(text="ok", provider="claude")
                yield TurnComplete(session_id="s", cost_usd=0.0, provider="claude")

            with patch("store.ai.create_session", side_effect=mock_stream):
                async for _ in orch.chat(branch_node.id, "follow-up"):
                    pass

        assert mock_resolve.called, (
            "resolve_session_continuity was never called for a child of a branch node — "
            "ancestor context fallback is silently bypassed"
        )

    @pytest.mark.asyncio
    async def test_branch_from_sessionless_node_stays_sessionless(self, orch, project):
        """Branching from a node with no session should correctly yield no session.

        This is the non-bug case: root node has no session, branch should also
        have no session. Verifies the fix doesn't over-correct.
        """
        git_branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=git_branch)
        assert root.session_id is None

        branch_node = await orch.branch(root.id)

        assert branch_node.session_id is None

    @pytest.mark.asyncio
    async def test_branch_still_inherits_git_commit(self, orch, project):
        """branch() must continue to inherit git_commit correctly (regression guard).

        This already passes — including it to ensure the fix for session_id
        doesn't break the git_commit inheritance.
        """
        git_branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=git_branch)

        parent = await _run_chat(orch, root.id, "first question")
        assert parent.git_commit is not None

        branch_node = await orch.branch(parent.id)

        assert branch_node.git_commit == parent.git_commit
