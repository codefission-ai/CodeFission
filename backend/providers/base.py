"""Abstract base for LLM providers.

Copied from WhatTheBot's core/providers/base.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class StreamEvent:
    type: str  # "text_delta" | "tool_call_start" | "tool_call_end" | "usage" | "done"
    text: str = ""
    tool_call: ToolCall | None = None
    tool_name: str = ""  # for tool_call_start (UI hint)
    usage: TokenUsage | None = None


@dataclass
class ProviderMessage:
    role: str  # "system" | "user" | "assistant" | "tool_result"
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""  # for tool_result messages
    tool_name: str = ""  # for tool_result messages (Anthropic needs this)


class ProviderBase(ABC):
    model: str

    @abstractmethod
    async def stream(
        self, messages: list[ProviderMessage], tools: list[dict] | None = None
    ) -> AsyncIterator[StreamEvent]:
        ...

    @abstractmethod
    def format_tools(self, tool_defs: list[dict]) -> list[dict]:
        """Convert generic tool definitions to provider-specific format."""
        ...

    @abstractmethod
    def format_tool_result(
        self, tool_call_id: str, tool_name: str, result: str, is_error: bool
    ) -> ProviderMessage:
        ...

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate: ~4 chars per token."""
        return max(1, len(text) // 4)

    @property
    @abstractmethod
    def context_window(self) -> int:
        ...

    @abstractmethod
    def cost_per_token(self) -> tuple[float, float]:
        """Return (input_cost_per_token, output_cost_per_token)."""
        ...
