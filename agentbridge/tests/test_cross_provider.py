"""End-to-end tests for cross-provider context transfer."""

from dataclasses import asdict
from pathlib import Path

import pytest

from agentbridge.context import (
    ConversationHistory,
    Message,
    build_context_prompt,
    extract_history,
    format_history_as_context,
)
from agentbridge.events import (
    SessionInit,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnComplete,
)
from agentbridge.types import ProviderType, SessionConfig


def _simulate_claude_session() -> list[dict]:
    """Simulate serialized events from a Claude session."""
    events = [
        asdict(SessionInit(session_id="claude-sess-1", provider="claude")),
        asdict(TextDelta(text="Let me check the files.", provider="claude")),
        asdict(ToolStart(tool_call_id="tc1", name="Bash", arguments={"command": "ls"}, provider="claude")),
        asdict(ToolEnd(tool_call_id="tc1", name="Bash", result="main.py\ntest.py\nREADME.md", provider="claude")),
        asdict(TextDelta(text="I found 3 files: main.py, test.py, and README.md.", provider="claude")),
        asdict(TurnComplete(
            session_id="claude-sess-1",
            is_error=False,
            cost_usd=0.02,
            token_usage={"input_tokens": 200, "output_tokens": 100},
            provider="claude",
        )),
    ]
    return events


def _simulate_codex_session() -> list[dict]:
    """Simulate serialized events from a Codex session."""
    events = [
        asdict(SessionInit(session_id="codex-thread-1", provider="codex")),
        asdict(ToolStart(tool_call_id="cmd1", name="bash", arguments={"command": "cat main.py"}, provider="codex")),
        asdict(ToolEnd(tool_call_id="cmd1", name="bash", result="print('hello')", provider="codex")),
        asdict(TextDelta(text="The main.py file contains a simple hello world script.", provider="codex")),
        asdict(TurnComplete(
            session_id="codex-thread-1",
            is_error=False,
            cost_usd=0.005,
            token_usage={"input_tokens": 150, "output_tokens": 80},
            provider="codex",
        )),
    ]
    return events


class TestClaudeToCodexTransfer:
    def test_extract_and_format(self):
        events = _simulate_claude_session()
        history = extract_history(events)

        assert history.provider == "claude"
        assert history.session_id == "claude-sess-1"
        assert len(history.messages) == 4  # text + tool_start + tool_end + text

        context = format_history_as_context(history)
        assert "claude" in context
        assert "claude-sess-1" in context
        assert "Let me check the files." in context
        assert "main.py" in context

    def test_build_prompt_for_codex(self):
        events = _simulate_claude_session()
        history = extract_history(events)
        prompt = build_context_prompt(history, "Now add type hints to main.py")

        assert "[Context from previous claude session" in prompt
        assert "Now add type hints to main.py" in prompt
        assert "[End of previous context]" in prompt

    def test_config_with_prior_context(self):
        events = _simulate_claude_session()
        history = extract_history(events)
        context = format_history_as_context(history)

        config = SessionConfig(
            provider=ProviderType.CODEX,
            prompt="Continue the work",
            cwd=Path("/tmp"),
            prior_context=context,
        )
        assert config.prior_context is not None
        assert "claude" in config.prior_context


class TestCodexToClaudeTransfer:
    def test_extract_and_format(self):
        events = _simulate_codex_session()
        history = extract_history(events)

        assert history.provider == "codex"
        assert history.session_id == "codex-thread-1"

        context = format_history_as_context(history)
        assert "codex" in context
        assert "print('hello')" in context

    def test_build_prompt_for_claude(self):
        events = _simulate_codex_session()
        history = extract_history(events)
        prompt = build_context_prompt(history, "Refactor this code")

        assert "[Context from previous codex session" in prompt
        assert "Refactor this code" in prompt


class TestRoundTrip:
    """Test full round-trip: provider A → extract → format → provider B config."""

    def test_claude_to_codex_roundtrip(self):
        # Session 1: Claude
        claude_events = _simulate_claude_session()
        history = extract_history(claude_events)
        context = format_history_as_context(history)

        # Session 2: Codex with prior context
        config = SessionConfig(
            provider=ProviderType.CODEX,
            prompt="Add tests for the files you found",
            cwd=Path("/tmp"),
            prior_context=context,
        )

        # Verify the config is well-formed
        assert config.prior_context is not None
        assert len(config.prior_context) > 0
        assert "Let me check the files" in config.prior_context
        assert config.prompt == "Add tests for the files you found"

    def test_codex_to_claude_roundtrip(self):
        codex_events = _simulate_codex_session()
        history = extract_history(codex_events)
        context = format_history_as_context(history)

        config = SessionConfig(
            provider=ProviderType.CLAUDE,
            prompt="Review the code quality",
            cwd=Path("/tmp"),
            prior_context=context,
        )

        assert config.prior_context is not None
        assert "print('hello')" in config.prior_context

    def test_empty_session_no_context(self):
        history = extract_history([])
        context = format_history_as_context(history)
        prompt = build_context_prompt(history, "Start fresh")

        assert context == ""
        assert prompt == "Start fresh"

    def test_multiple_tool_calls(self):
        events = [
            asdict(SessionInit(session_id="s1", provider="claude")),
            asdict(TextDelta(text="Running multiple commands.", provider="claude")),
            asdict(ToolStart(tool_call_id="tc1", name="Bash", arguments={"command": "ls"}, provider="claude")),
            asdict(ToolEnd(tool_call_id="tc1", name="Bash", result="a.py\nb.py", provider="claude")),
            asdict(ToolStart(tool_call_id="tc2", name="Read", arguments={"path": "a.py"}, provider="claude")),
            asdict(ToolEnd(tool_call_id="tc2", name="Read", result="# a.py contents", provider="claude")),
            asdict(TextDelta(text="Both files checked.", provider="claude")),
            asdict(TurnComplete(session_id="s1", is_error=False, provider="claude")),
        ]
        history = extract_history(events)
        assert len(history.messages) == 6  # text + 2*(start+end) + text

        context = format_history_as_context(history)
        assert "Bash" in context
        assert "Read" in context
        assert "a.py" in context
