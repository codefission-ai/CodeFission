"""LLM provider abstraction layer.

Adapted from WhatTheBot's core/providers — normalises Anthropic and OpenAI
streaming behind a single ProviderBase interface so Clawtree can swap models
per tree or per branch.
"""

from .base import ProviderBase, ProviderMessage, StreamEvent, TokenUsage, ToolCall


def create_provider(provider: str, model: str) -> ProviderBase:
    """Factory: return the right provider for a (provider, model) pair."""
    if provider == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(model)
    elif provider == "openai":
        from .openai_provider import OpenAIProvider
        return OpenAIProvider(model)
    else:
        raise ValueError(f"Unknown provider: {provider!r}")
