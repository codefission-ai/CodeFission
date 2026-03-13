"""Phase 4B — Test provider/model columns on nodes.

Tests that:
  - DB migration adds provider and model columns to nodes
  - Chat saves provider/model on the node after completion
  - Root node has null provider
  - Mid-tree provider switches are recorded correctly per-node

Written against the PLANNED interface from backend-rewrite-plan.md.
Will fail until the implementation is done (provider/model columns on nodes
and Orchestrator saving them after chat).
"""

from unittest.mock import patch

import pytest

from agentbridge import SessionInit, TextDelta, TurnComplete
from services.orchestrator import Orchestrator
from services.tree_service import get_node, get_tree, create_tree, update_node
from services.workspace_service import _run_git, _GIT_ENV


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


async def _make_mock_stream(provider="claude", session_id="sess-1", cost=0.01):
    """Return an async generator that yields a minimal event sequence."""
    async def stream(*args, **kwargs):
        yield SessionInit(session_id=session_id, provider=provider)
        yield TextDelta(text="Hello", provider=provider)
        yield TurnComplete(session_id=session_id, cost_usd=cost, provider=provider)
    return stream


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

class TestNodeProviderModel:

    @pytest.mark.asyncio
    async def test_provider_column_migration(self, tmp_db):
        """After DB init, nodes table has provider and model columns."""
        from db import get_db

        async with get_db() as db:
            cursor = await db.execute("PRAGMA table_info(nodes)")
            columns = {row[1] for row in await cursor.fetchall()}

        assert "provider" in columns
        assert "model" in columns

    @pytest.mark.asyncio
    async def test_chat_saves_provider_on_node(self, orch, project):
        """After chat completion, node.provider is set to the provider used."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)

        mock_fn = await _make_mock_stream(provider="claude")

        with patch("services.chat_service.stream_chat", side_effect=mock_fn):
            node_id = None
            async for event in orch.chat(root.id, "hello"):
                if type(event).__name__ == "ChatNodeCreated":
                    node_id = event.node.id

        assert node_id is not None
        node = await get_node(node_id)
        assert node.provider == "claude"

    @pytest.mark.asyncio
    async def test_chat_saves_model_on_node(self, orch, project):
        """After chat completion, node.model is set to the model used."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)

        mock_fn = await _make_mock_stream(provider="claude")

        with patch("services.chat_service.stream_chat", side_effect=mock_fn):
            node_id = None
            async for event in orch.chat(root.id, "hello"):
                if type(event).__name__ == "ChatNodeCreated":
                    node_id = event.node.id

        node = await get_node(node_id)
        assert node.model is not None
        assert len(node.model) > 0

    @pytest.mark.asyncio
    async def test_root_has_null_provider(self, orch, project):
        """Root node has provider=None (no chat was run on it)."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)

        node = await get_node(root.id)
        # Root has no provider (no chat happened)
        assert node.provider is None or node.provider == ""

    @pytest.mark.asyncio
    async def test_mid_tree_provider_switch(self, orch, project):
        """Provider switches mid-tree are recorded on each node correctly.

        n2 uses claude, n3 uses codex, n4 uses claude.
        Each node records the provider that was actually used.
        """
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)

        # n2: claude
        mock_claude = await _make_mock_stream(provider="claude", session_id="sess-c1")
        with patch("services.chat_service.stream_chat", side_effect=mock_claude):
            n2_id = None
            async for event in orch.chat(root.id, "first (claude)"):
                if type(event).__name__ == "ChatNodeCreated":
                    n2_id = event.node.id

        # n3: codex (child of n2)
        mock_codex = await _make_mock_stream(provider="codex", session_id="sess-x1")
        with patch("services.chat_service.stream_chat", side_effect=mock_codex):
            n3_id = None
            async for event in orch.chat(n2_id, "second (codex)"):
                if type(event).__name__ == "ChatNodeCreated":
                    n3_id = event.node.id

        # n4: claude again (child of n3)
        mock_claude2 = await _make_mock_stream(provider="claude", session_id="sess-c2")
        with patch("services.chat_service.stream_chat", side_effect=mock_claude2):
            n4_id = None
            async for event in orch.chat(n3_id, "third (claude again)"):
                if type(event).__name__ == "ChatNodeCreated":
                    n4_id = event.node.id

        # Verify each node recorded the correct provider
        n2 = await get_node(n2_id)
        assert n2.provider == "claude"

        n3 = await get_node(n3_id)
        assert n3.provider == "codex"

        n4 = await get_node(n4_id)
        assert n4.provider == "claude"
