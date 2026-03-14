"""Compatibility shim — re-exports from services.chat.

This file exists so old imports like `from services.chat_service import ...`
continue to work. New code should import from `services.chat` instead.
"""

from services.chat import *  # noqa: F401,F403
from services.chat import (  # explicit re-exports for type checkers
    stream_chat,
    resolve_session_continuity,
    TextDelta,
    ToolStart,
    ToolEnd,
    SessionInit,
    TurnComplete,
    _build_system_prompt,
    _build_context_from_ancestors,
    _sdk_env,
)
