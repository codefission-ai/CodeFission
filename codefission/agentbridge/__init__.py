"""AgentBridge — unified async interface for AI coding CLI tools."""

from __future__ import annotations

from typing import AsyncGenerator

from .adapters import get_adapter
from .base import BaseAdapter
from .context import (
    ConversationHistory,
    Message,
    build_context_prompt,
    extract_history,
    format_history_as_context,
)
from .discovery import AuthInfo, ProviderInfo, discover, discover_sync
from .events import (
    BridgeEvent,
    SessionInit,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnComplete,
)
from .pricing import (
    PRICING_TABLE,
    ModelPricing,
    TokenUsage,
    cheapest_model,
    estimate_cost,
    estimate_cost_from_raw,
)
from .session_manager import SessionManager, SwitchResult
from .subprocess_runner import SubprocessRunner
from .types import PermissionLevel, ProviderType, SessionConfig, resolve_permission

__all__ = [
    "create_session",
    "create_session_with_context",
    "BaseAdapter",
    "BridgeEvent",
    "TextDelta",
    "ToolStart",
    "ToolEnd",
    "SessionInit",
    "TurnComplete",
    "PermissionLevel",
    "ProviderType",
    "SessionConfig",
    "resolve_permission",
    "ProviderInfo",
    "AuthInfo",
    "discover",
    "discover_sync",
    "get_adapter",
    # Pricing
    "ModelPricing",
    "TokenUsage",
    "PRICING_TABLE",
    "estimate_cost",
    "estimate_cost_from_raw",
    # Session management
    "SessionManager",
    "SwitchResult",
    # Context transfer
    "Message",
    "ConversationHistory",
    "extract_history",
    "format_history_as_context",
    "build_context_prompt",
]


async def create_session(
    config: SessionConfig,
) -> AsyncGenerator[BridgeEvent, None]:
    """High-level API: spawn a provider CLI and yield unified BridgeEvents.

    Usage::

        async for event in create_session(SessionConfig(
            provider=ProviderType.CLAUDE,
            prompt="What files are in this directory?",
        )):
            if isinstance(event, TextDelta):
                print(event.text, end="")
    """
    adapter = get_adapter(config.provider)
    cmd = adapter.build_command(config)
    env = adapter.build_env(config)
    runner = SubprocessRunner(cmd=cmd, cwd=config.cwd, env=env)

    try:
        await runner.start()
        async for event in adapter.stream(runner, config):
            yield event
    finally:
        await runner.close()


async def create_session_with_context(
    config: SessionConfig,
    prior_events: list[dict],
) -> AsyncGenerator[BridgeEvent, None]:
    """Spawn a session with context from a prior session's events.

    Extracts conversation history from prior_events, formats it as text,
    and injects it into the config's prior_context field.

    Usage::

        async for event in create_session_with_context(
            SessionConfig(provider=ProviderType.CODEX, prompt="Continue this work"),
            prior_events=serialized_events_from_claude,
        ):
            ...
    """
    history = extract_history(prior_events)
    context = format_history_as_context(history)
    if context:
        config.prior_context = context
    async for event in create_session(config):
        yield event
