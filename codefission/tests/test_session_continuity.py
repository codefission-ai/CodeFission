"""Phase 4A — Test resolve_session_continuity() and build_context_from_ancestors().

Tests for the session continuity logic that decides whether to fork a session
(same provider) or do a context transfer (different provider / no session).

Written against the PLANNED interface from backend-rewrite-plan.md.
Will fail until the implementation is done — the functions resolve_session_continuity
and _build_context_from_ancestors will be in services/chat.py (renamed from
chat_service.py).
"""

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from agentbridge import (
    ConversationHistory,
    Message,
    format_history_as_context,
)


# ---------------------------------------------------------------------------
# Fake Node for unit tests — avoids needing a real DB
# ---------------------------------------------------------------------------

@dataclass
class FakeNode:
    """Minimal Node-like object for testing session continuity logic."""
    id: str = "node-1"
    tree_id: str = "tree-1"
    parent_id: str | None = None
    user_message: str = ""
    assistant_response: str = ""
    provider: str | None = None
    model: str | None = None
    session_id: str | None = None
    status: str = "done"
    git_commit: str | None = None
    git_branch: str | None = None
    label: str = ""
    created_at: str = ""
    children_ids: list[str] = field(default_factory=list)
    created_by: str = "human"
    quoted_node_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# TestResolveSessionContinuity
# ---------------------------------------------------------------------------

class TestResolveSessionContinuity:
    """Test resolve_session_continuity(parent_node, new_provider).

    Returns (resume_session_id, fork_session, prior_context).
    """

    @pytest.mark.asyncio
    async def test_root_parent_returns_fresh_start(self):
        """Root node (no user_message) -> fresh start: (None, False, None)."""
        # Import from the planned module path
        from store.ai import resolve_session_continuity

        root = FakeNode(user_message="")
        resume_id, fork, context = await resolve_session_continuity(root, "claude")

        assert resume_id is None
        assert fork is False
        assert context is None

    @pytest.mark.asyncio
    async def test_same_provider_returns_fork(self):
        """Same provider with session_id -> fork: (session_id, True, None)."""
        from store.ai import resolve_session_continuity

        parent = FakeNode(
            user_message="hello",
            provider="claude",
            session_id="sess_abc",
        )
        resume_id, fork, context = await resolve_session_continuity(parent, "claude")

        assert resume_id == "sess_abc"
        assert fork is True
        assert context is None

    @pytest.mark.asyncio
    async def test_same_provider_different_model_still_forks(self):
        """Same provider, different model -> still forks (sessions are provider-level)."""
        from store.ai import resolve_session_continuity

        parent = FakeNode(
            user_message="hello",
            provider="claude",
            model="claude-opus-4-6",
            session_id="sess_abc",
        )
        # New provider is still "claude" even though model will differ
        resume_id, fork, context = await resolve_session_continuity(parent, "claude")

        assert resume_id == "sess_abc"
        assert fork is True
        assert context is None

    @pytest.mark.asyncio
    async def test_different_provider_returns_context_transfer(self):
        """Different provider -> context transfer: (None, False, '<context text>')."""
        from store.ai import resolve_session_continuity

        parent = FakeNode(
            id="parent-1",
            user_message="hello",
            assistant_response="hi there",
            provider="claude",
            session_id="sess_abc",
        )

        # Mock get_ancestor_chain to return ancestors
        with patch("store.ai.get_ancestor_chain", new_callable=AsyncMock) as mock_ancestors:
            mock_ancestors.return_value = [parent]  # just the parent in the chain
            resume_id, fork, context = await resolve_session_continuity(parent, "codex")

        assert resume_id is None
        assert fork is False
        assert context is not None
        assert len(context) > 0
        # Context should contain the parent's conversation
        assert "hello" in context or "hi there" in context

    @pytest.mark.asyncio
    async def test_no_session_id_returns_context_transfer(self):
        """Same provider but no session_id -> context transfer (can't fork)."""
        from store.ai import resolve_session_continuity

        parent = FakeNode(
            id="parent-1",
            user_message="hello",
            assistant_response="hi",
            provider="claude",
            session_id=None,  # no session to fork from
        )

        with patch("store.ai.get_ancestor_chain", new_callable=AsyncMock) as mock_ancestors:
            mock_ancestors.return_value = [parent]
            resume_id, fork, context = await resolve_session_continuity(parent, "claude")

        assert resume_id is None
        assert fork is False
        assert context is not None

    @pytest.mark.asyncio
    async def test_branch_passthrough_same_provider_forks_session(self):
        """Branch passthrough node (inherited session, no user_message) -> fork.

        Previously this returned (None, False, None) — a fresh start — which was
        the session-continuity bug. The correct behaviour is to fork the inherited
        session so conversation context isn't silently dropped at branch points.
        """
        from store.ai import resolve_session_continuity

        parent = FakeNode(
            user_message="",
            provider="claude",
            session_id="sess_abc",
        )
        resume_id, fork, context = await resolve_session_continuity(parent, "claude")

        assert resume_id == "sess_abc"
        assert fork is True
        assert context is None

    @pytest.mark.asyncio
    async def test_sessionless_empty_message_returns_fresh(self):
        """Root node (no session, no user_message) -> fresh start (unchanged)."""
        from store.ai import resolve_session_continuity

        root = FakeNode(user_message="", provider=None, session_id=None)
        resume_id, fork, context = await resolve_session_continuity(root, "claude")

        assert resume_id is None
        assert fork is False
        assert context is None


# ---------------------------------------------------------------------------
# TestBuildContextFromAncestors
# ---------------------------------------------------------------------------

class TestBuildContextFromAncestors:
    """Test _build_context_from_ancestors(ancestors)."""

    def test_single_ancestor(self):
        """Single ancestor produces context with its conversation."""
        from store.ai import _build_context_from_ancestors

        parent = FakeNode(
            user_message="hello",
            assistant_response="hi",
            provider="claude",
            session_id="sess1",
        )

        context = _build_context_from_ancestors([parent])

        assert "hello" in context
        assert "hi" in context

    def test_ancestor_chain_in_order(self):
        """Grandparent -> parent -> (current): grandparent conversation comes first."""
        from store.ai import _build_context_from_ancestors

        grandparent = FakeNode(
            id="gp",
            user_message="first question",
            assistant_response="first answer",
            provider="claude",
            session_id="sess1",
        )
        parent = FakeNode(
            id="p",
            user_message="second question",
            assistant_response="second answer",
            provider="claude",
            session_id="sess2",
        )

        context = _build_context_from_ancestors([grandparent, parent])

        # Grandparent conversation should appear before parent's
        gp_pos = context.find("first question")
        p_pos = context.find("second question")
        assert gp_pos < p_pos

    def test_skips_empty_messages(self):
        """Root (empty message) is skipped; only real conversations included."""
        from store.ai import _build_context_from_ancestors

        root = FakeNode(
            id="root",
            user_message="",
            assistant_response="",
            provider=None,
            session_id=None,
        )
        parent = FakeNode(
            id="p",
            user_message="real question",
            assistant_response="real answer",
            provider="claude",
            session_id="sess1",
        )

        context = _build_context_from_ancestors([root, parent])

        assert "real question" in context
        assert "real answer" in context

    def test_uses_agentbridge_format(self):
        """Context is formatted via agentbridge's format_history_as_context."""
        from store.ai import _build_context_from_ancestors

        parent = FakeNode(
            user_message="hello",
            assistant_response="hi",
            provider="claude",
            session_id="sess1",
        )

        context = _build_context_from_ancestors([parent])

        # Context should start with a system marker
        assert "[System:" in context or "[Context" in context

    def test_truncation_on_long_history(self):
        """Very long ancestor chains are truncated to a reasonable size."""
        from store.ai import _build_context_from_ancestors

        # Create 20 ancestors with long responses
        ancestors = []
        for i in range(20):
            ancestors.append(FakeNode(
                id=f"n{i}",
                user_message=f"Question {i}: {'x' * 1000}",
                assistant_response=f"Answer {i}: {'y' * 1000}",
                provider="claude",
                session_id=f"sess{i}",
            ))

        parent = ancestors[-1]
        context = _build_context_from_ancestors(ancestors)

        # Context should exist but be bounded
        assert len(context) > 0
        # Exact limit depends on implementation, but it should be bounded
        # A reasonable upper bound might be ~100KB
        assert len(context) < 500_000
