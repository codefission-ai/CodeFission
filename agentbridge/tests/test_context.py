"""Tests for the context transfer module."""

from agentbridge.context import (
    ConversationHistory,
    Message,
    build_context_prompt,
    extract_history,
    format_history_as_context,
)


class TestExtractHistory:
    def test_empty_events(self):
        history = extract_history([])
        assert history.provider == ""
        assert history.session_id == ""
        assert history.messages == []

    def test_session_init(self):
        events = [
            {"kind": "session_init", "provider": "claude", "session_id": "abc-123"},
        ]
        history = extract_history(events)
        assert history.provider == "claude"
        assert history.session_id == "abc-123"

    def test_text_deltas_merged(self):
        events = [
            {"kind": "text_delta", "text": "Hello "},
            {"kind": "text_delta", "text": "world!"},
            {"kind": "turn_complete"},
        ]
        history = extract_history(events)
        assert len(history.messages) == 1
        assert history.messages[0].role == "assistant"
        assert history.messages[0].content == "Hello world!"

    def test_tool_start_flushes_text(self):
        events = [
            {"kind": "text_delta", "text": "Let me check."},
            {"kind": "tool_start", "name": "bash", "tool_call_id": "tc1"},
            {"kind": "tool_end", "name": "bash", "tool_call_id": "tc1", "result": "file.txt", "is_error": False},
            {"kind": "text_delta", "text": "Found it."},
            {"kind": "turn_complete"},
        ]
        history = extract_history(events)
        assert len(history.messages) == 4
        assert history.messages[0].role == "assistant"
        assert history.messages[0].content == "Let me check."
        assert history.messages[1].role == "tool"
        assert history.messages[1].tool_name == "bash"
        assert history.messages[2].role == "tool"
        assert history.messages[2].content == "file.txt"
        assert history.messages[3].role == "assistant"
        assert history.messages[3].content == "Found it."

    def test_tool_error_flagged(self):
        events = [
            {"kind": "tool_end", "name": "bash", "tool_call_id": "tc1", "result": "command not found", "is_error": True},
        ]
        history = extract_history(events)
        assert history.messages[0].is_error is True

    def test_long_result_truncated(self):
        long_result = "x" * 1000
        events = [
            {"kind": "tool_end", "name": "bash", "tool_call_id": "tc1", "result": long_result, "is_error": False},
        ]
        history = extract_history(events)
        assert len(history.messages[0].content) == 503  # 500 + "..."
        assert history.messages[0].content.endswith("...")

    def test_trailing_text_flushed(self):
        events = [
            {"kind": "text_delta", "text": "Done."},
        ]
        history = extract_history(events)
        assert len(history.messages) == 1
        assert history.messages[0].content == "Done."


class TestFormatHistory:
    def test_empty_history(self):
        history = ConversationHistory(provider="claude", session_id="s1")
        assert format_history_as_context(history) == ""

    def test_basic_formatting(self):
        history = ConversationHistory(
            provider="claude",
            session_id="s1",
            messages=[
                Message(role="assistant", content="Hello!"),
                Message(role="tool", content="Called bash", tool_name="bash"),
                Message(role="tool", content="file.txt", tool_name="bash", is_error=False),
            ],
        )
        result = format_history_as_context(history)
        assert "[Context from previous claude session s1]" in result
        assert "Assistant: Hello!" in result
        assert "[Tool: bash]" in result
        assert "[Result: file.txt]" in result
        assert "[End of previous context]" in result

    def test_error_result(self):
        history = ConversationHistory(
            provider="codex",
            session_id="s2",
            messages=[
                Message(role="tool", content="segfault", is_error=True),
            ],
        )
        result = format_history_as_context(history)
        assert "[ERROR: segfault]" in result


class TestBuildContextPrompt:
    def test_with_context(self):
        history = ConversationHistory(
            provider="claude",
            session_id="s1",
            messages=[Message(role="assistant", content="I found a bug.")],
        )
        result = build_context_prompt(history, "Fix it please")
        assert result.startswith("[Context from previous claude session s1]")
        assert result.endswith("Fix it please")

    def test_without_context(self):
        history = ConversationHistory(provider="", session_id="")
        result = build_context_prompt(history, "Hello")
        assert result == "Hello"
