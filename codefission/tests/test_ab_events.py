"""Tests for unified event types."""

from agentbridge.events import (
    BridgeEvent,
    SessionInit,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnComplete,
)


class TestEventConstruction:
    def test_text_delta(self):
        e = TextDelta(text="hello", provider="claude")
        assert e.kind == "text_delta"
        assert e.text == "hello"
        assert e.provider == "claude"
        assert e.raw is None

    def test_tool_start(self):
        e = ToolStart(
            tool_call_id="tc1",
            name="bash",
            arguments={"command": "ls"},
            provider="codex",
        )
        assert e.kind == "tool_start"
        assert e.tool_call_id == "tc1"
        assert e.name == "bash"
        assert e.arguments == {"command": "ls"}

    def test_tool_start_default_arguments(self):
        e = ToolStart(tool_call_id="tc2", name="read")
        assert e.arguments == {}

    def test_tool_end(self):
        e = ToolEnd(
            tool_call_id="tc1",
            name="bash",
            result="file.txt",
            is_error=False,
            provider="claude",
        )
        assert e.kind == "tool_end"
        assert e.result == "file.txt"
        assert e.is_error is False

    def test_tool_end_error(self):
        e = ToolEnd(tool_call_id="tc1", name="bash", result="fail", is_error=True)
        assert e.is_error is True

    def test_session_init(self):
        e = SessionInit(session_id="abc-123", provider="claude")
        assert e.kind == "session_init"
        assert e.session_id == "abc-123"

    def test_turn_complete_minimal(self):
        e = TurnComplete(session_id="s1", is_error=False)
        assert e.kind == "turn_complete"
        assert e.cost_usd is None
        assert e.duration_ms is None
        assert e.num_turns is None
        assert e.token_usage is None

    def test_turn_complete_full(self):
        e = TurnComplete(
            session_id="s1",
            is_error=False,
            duration_ms=1234,
            cost_usd=0.05,
            num_turns=3,
            token_usage={"input_tokens": 100, "output_tokens": 50},
            provider="claude",
        )
        assert e.duration_ms == 1234
        assert e.cost_usd == 0.05
        assert e.num_turns == 3
        assert e.token_usage["input_tokens"] == 100

    def test_bridge_event_base(self):
        e = BridgeEvent(kind="custom", provider="test")
        assert e.kind == "custom"
        assert e.raw is None

    def test_raw_preserved(self):
        raw = {"type": "test", "data": 42}
        e = TextDelta(text="hi", provider="claude", raw=raw)
        assert e.raw is raw
