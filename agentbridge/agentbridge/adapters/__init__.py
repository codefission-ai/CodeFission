"""Adapter registry."""

from __future__ import annotations

from ..base import BaseAdapter
from ..types import ProviderType
from .claude import ClaudeAdapter
from .codex import CodexAdapter

_ADAPTERS: dict[ProviderType, type[BaseAdapter]] = {
    ProviderType.CLAUDE: ClaudeAdapter,
    ProviderType.CODEX: CodexAdapter,
}


def get_adapter(provider: ProviderType) -> BaseAdapter:
    cls = _ADAPTERS.get(provider)
    if cls is None:
        raise ValueError(f"No adapter for provider: {provider}")
    return cls()
