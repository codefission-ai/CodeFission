"""Unified event types emitted by all provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BridgeEvent:
    """Base event yielded by adapter stream."""
    kind: str
    provider: str  # "claude-code" | "codex"
    raw: dict | None = None  # original provider JSON for debugging


@dataclass
class TextDelta(BridgeEvent):
    """Incremental text from the assistant response."""
    text: str = ""

    def __init__(self, text: str, provider: str = "", raw: dict | None = None):
        self.kind = "text_delta"
        self.provider = provider
        self.text = text
        self.raw = raw


@dataclass
class ToolStart(BridgeEvent):
    """A tool invocation has started."""
    tool_call_id: str = ""
    name: str = ""
    arguments: dict = field(default_factory=dict)

    def __init__(
        self,
        tool_call_id: str,
        name: str,
        arguments: dict | None = None,
        provider: str = "",
        raw: dict | None = None,
    ):
        self.kind = "tool_start"
        self.provider = provider
        self.tool_call_id = tool_call_id
        self.name = name
        self.arguments = arguments or {}
        self.raw = raw


@dataclass
class ToolEnd(BridgeEvent):
    """A tool invocation has completed."""
    tool_call_id: str = ""
    name: str = ""
    result: str = ""
    is_error: bool = False

    def __init__(
        self,
        tool_call_id: str,
        name: str,
        result: str = "",
        is_error: bool = False,
        provider: str = "",
        raw: dict | None = None,
    ):
        self.kind = "tool_end"
        self.provider = provider
        self.tool_call_id = tool_call_id
        self.name = name
        self.result = result
        self.is_error = is_error
        self.raw = raw


@dataclass
class SessionInit(BridgeEvent):
    """Emitted once when the session/thread ID is known."""
    session_id: str = ""

    def __init__(self, session_id: str, provider: str = "", raw: dict | None = None):
        self.kind = "session_init"
        self.provider = provider
        self.session_id = session_id
        self.raw = raw


@dataclass
class TurnComplete(BridgeEvent):
    """Emitted when the agent finishes its turn."""
    session_id: str = ""
    is_error: bool = False
    duration_ms: int | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    token_usage: dict | None = None  # {"input_tokens", "output_tokens", "cached_input_tokens"}

    def __init__(
        self,
        session_id: str = "",
        is_error: bool = False,
        duration_ms: int | None = None,
        cost_usd: float | None = None,
        num_turns: int | None = None,
        token_usage: dict | None = None,
        provider: str = "",
        raw: dict | None = None,
    ):
        self.kind = "turn_complete"
        self.provider = provider
        self.session_id = session_id
        self.is_error = is_error
        self.duration_ms = duration_ms
        self.cost_usd = cost_usd
        self.num_turns = num_turns
        self.token_usage = token_usage
        self.raw = raw
