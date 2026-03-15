"""Phase 1A — Test the Orchestrator's new chat() async generator method.

These tests mock agentbridge's stream_chat to yield canned BridgeEvent
sequences. They verify the domain-event protocol that the Orchestrator
exposes to both Presenters (WS and REST/CLI).

The chat() method is an async generator that yields:
  ChatNodeCreated -> TextDelta* / ToolStart / ToolEnd -> ChatCompleted

Written against the PLANNED interface from backend-rewrite-plan.md.
Will fail until the implementation is done.
"""

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from agentbridge import (
    BridgeEvent,
    SessionInit,
    TextDelta,
    ToolStart,
    ToolEnd,
    TurnComplete,
)
from orchestrator import Orchestrator
from store.trees import get_node, get_tree
from store.git import _run_git, _GIT_ENV


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


async def _mock_stream_chat_factory(
    events=None,
    provider="claude",
    session_id="test-session-123",
    cost_usd=0.01,
):
    """Return an async generator function that yields canned events."""
    if events is None:
        events = [
            SessionInit(session_id=session_id, provider=provider),
            TextDelta(text="Hello ", provider=provider),
            TextDelta(text="world", provider=provider),
            TurnComplete(session_id=session_id, cost_usd=cost_usd, provider=provider),
        ]

    async def mock_stream_chat(*args, **kwargs):
        for evt in events:
            yield evt

    return mock_stream_chat


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

class TestOrchestratorChat:
    """Test chat() as an async generator yielding domain events."""

    @pytest.mark.asyncio
    async def test_chat_yields_node_created_first(self, orch, project):
        """First yielded event from chat() is ChatNodeCreated with a valid node."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)

        mock_fn = await _mock_stream_chat_factory()
        with patch("orchestrator.chat.stream_chat", side_effect=mock_fn):
            events = []
            async for event in orch.chat(root.id, "hello"):
                events.append(event)
                break  # only need the first event

        first = events[0]
        # The planned interface names this event ChatNodeCreated
        assert type(first).__name__ == "ChatNodeCreated"
        assert hasattr(first, "node")
        assert first.node.tree_id == tree.id
        assert first.node.status == "active"

        # Verify node exists in DB
        node = await get_node(first.node.id)
        assert node is not None
        assert node.status == "active"

    @pytest.mark.asyncio
    async def test_chat_yields_text_deltas(self, orch, project):
        """TextDelta events appear in order in the stream."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)

        mock_fn = await _mock_stream_chat_factory(events=[
            SessionInit(session_id="s1", provider="claude"),
            TextDelta(text="hello", provider="claude"),
            TextDelta(text=" world", provider="claude"),
            TurnComplete(session_id="s1", cost_usd=0.01, provider="claude"),
        ])

        with patch("orchestrator.chat.stream_chat", side_effect=mock_fn):
            text_deltas = []
            async for event in orch.chat(root.id, "greet me"):
                if isinstance(event, TextDelta):
                    text_deltas.append(event.text)

        assert text_deltas == ["hello", " world"]

    @pytest.mark.asyncio
    async def test_chat_yields_tool_events(self, orch, project):
        """ToolStart and ToolEnd events are forwarded through."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)

        mock_fn = await _mock_stream_chat_factory(events=[
            SessionInit(session_id="s1", provider="claude"),
            TextDelta(text="Let me run that", provider="claude"),
            ToolStart(tool_call_id="tc1", name="Bash", provider="claude"),
            ToolEnd(tool_call_id="tc1", name="Bash", result="ok", provider="claude"),
            TextDelta(text="Done", provider="claude"),
            TurnComplete(session_id="s1", cost_usd=0.02, provider="claude"),
        ])

        with patch("orchestrator.chat.stream_chat", side_effect=mock_fn):
            tool_events = []
            async for event in orch.chat(root.id, "run ls"):
                if isinstance(event, (ToolStart, ToolEnd)):
                    tool_events.append(event)

        assert len(tool_events) == 2
        assert isinstance(tool_events[0], ToolStart)
        assert tool_events[0].name == "Bash"
        assert isinstance(tool_events[1], ToolEnd)
        assert tool_events[1].result == "ok"

    @pytest.mark.asyncio
    async def test_chat_yields_completed_last(self, orch, project):
        """Last event is ChatCompleted with result metadata."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)

        mock_fn = await _mock_stream_chat_factory()

        with patch("orchestrator.chat.stream_chat", side_effect=mock_fn):
            events = []
            async for event in orch.chat(root.id, "hello"):
                events.append(event)

        last = events[-1]
        assert type(last).__name__ == "ChatCompleted"
        assert hasattr(last, "result")
        assert hasattr(last.result, "files_changed")
        assert hasattr(last.result, "git_commit")

    @pytest.mark.asyncio
    async def test_chat_saves_response_on_completion(self, orch, project):
        """After consuming all events, node in DB has status=done and response set."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)

        mock_fn = await _mock_stream_chat_factory(events=[
            SessionInit(session_id="s1", provider="claude"),
            TextDelta(text="Here is my ", provider="claude"),
            TextDelta(text="response", provider="claude"),
            TurnComplete(session_id="s1", cost_usd=0.01, provider="claude"),
        ])

        with patch("orchestrator.chat.stream_chat", side_effect=mock_fn):
            node_id = None
            async for event in orch.chat(root.id, "hello"):
                if type(event).__name__ == "ChatNodeCreated":
                    node_id = event.node.id

        assert node_id is not None
        node = await get_node(node_id)
        assert node.status == "done"
        assert node.assistant_response == "Here is my response"
        assert node.git_commit is not None

    @pytest.mark.asyncio
    async def test_chat_records_provider_and_model_on_node(self, orch, project):
        """After completion, node records provider and model that were used."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)

        mock_fn = await _mock_stream_chat_factory(
            provider="claude",
            session_id="s1",
        )

        with patch("orchestrator.chat.stream_chat", side_effect=mock_fn):
            node_id = None
            async for event in orch.chat(root.id, "hello"):
                if type(event).__name__ == "ChatNodeCreated":
                    node_id = event.node.id

        node = await get_node(node_id)
        assert node.provider == "claude"
        assert node.model is not None  # should be set to the resolved model

    @pytest.mark.asyncio
    async def test_chat_cancellation_yields_error(self, orch, project):
        """Cancelling mid-stream sets node status=error with cancellation marker."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)

        # Simulate a slow stream that can be cancelled
        async def slow_stream(*args, **kwargs):
            yield SessionInit(session_id="s1", provider="claude")
            yield TextDelta(text="Starting...", provider="claude")
            # Simulate delay that allows cancellation
            await asyncio.sleep(10)
            yield TurnComplete(session_id="s1", provider="claude")

        with patch("orchestrator.chat.stream_chat", side_effect=slow_stream):
            node_id = None
            async for event in orch.chat(root.id, "long task"):
                if type(event).__name__ == "ChatNodeCreated":
                    node_id = event.node.id
                    # Cancel the chat after getting the node
                    # The orchestrator should support cancellation
                    break

            # Cancel via orchestrator
            if node_id:
                await orch.cancel_chat(node_id, "Starting...", [])

        if node_id:
            node = await get_node(node_id)
            assert node.status == "error"
            assert "[Cancelled by user]" in node.assistant_response

    @pytest.mark.asyncio
    async def test_chat_exception_yields_error(self, orch, project):
        """If stream_chat raises an exception, node status becomes error."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)

        async def failing_stream(*args, **kwargs):
            yield SessionInit(session_id="s1", provider="claude")
            raise RuntimeError("Connection lost")

        with patch("orchestrator.chat.stream_chat", side_effect=failing_stream):
            node_id = None
            events = []
            try:
                async for event in orch.chat(root.id, "hello"):
                    events.append(event)
                    if type(event).__name__ == "ChatNodeCreated":
                        node_id = event.node.id
            except RuntimeError:
                pass  # The generator may propagate the error

        if node_id:
            node = await get_node(node_id)
            assert node.status == "error"

    @pytest.mark.asyncio
    async def test_chat_no_file_changes_still_completes(self, orch, project):
        """Even with no file edits, ChatCompleted has files_changed=0 and a commit."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)

        mock_fn = await _mock_stream_chat_factory(events=[
            SessionInit(session_id="s1", provider="claude"),
            TextDelta(text="Just an explanation", provider="claude"),
            TurnComplete(session_id="s1", cost_usd=0.005, provider="claude"),
        ])

        with patch("orchestrator.chat.stream_chat", side_effect=mock_fn):
            completed_event = None
            async for event in orch.chat(root.id, "explain something"):
                if type(event).__name__ == "ChatCompleted":
                    completed_event = event

        assert completed_event is not None
        assert completed_event.result.files_changed == 0
        # Node still gets a git_commit (parent's commit or new commit)
        assert completed_event.result.git_commit is not None
