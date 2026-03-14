"""Provider registry — lists supported coding tools, models, and auth modes.

Each provider represents a CLI tool (Claude Code, Codex, etc.) that can be
used as the backend for a tree's chat.
"""

from dataclasses import dataclass


@dataclass
class Provider:
    id: str
    name: str
    models: list[str]
    default_model: str
    auth_modes: list[str]           # e.g. ["cli", "api_key"]
    default_auth_mode: str


PROVIDERS: dict[str, Provider] = {
    "claude-code": Provider(
        id="claude-code",
        name="Claude Code",
        models=[
            "claude-sonnet-4-6",
            "claude-opus-4-6",
            "claude-haiku-4-5-20251001",
        ],
        default_model="claude-opus-4-6",
        auth_modes=["cli", "api_key"],
        default_auth_mode="cli",
    ),
    "codex": Provider(
        id="codex",
        name="Codex CLI",
        models=["o4-mini", "codex-mini"],
        default_model="o4-mini",
        auth_modes=["api_key"],
        default_auth_mode="api_key",
    ),
    "gemini-cli": Provider(
        id="gemini-cli",
        name="Gemini CLI",
        models=["gemini-2.5-pro", "gemini-2.5-flash"],
        default_model="gemini-2.5-pro",
        auth_modes=["api_key", "gcloud"],
        default_auth_mode="api_key",
    ),
    "aider": Provider(
        id="aider",
        name="Aider",
        models=["sonnet", "opus", "gpt-4o", "deepseek"],
        default_model="sonnet",
        auth_modes=["api_key"],
        default_auth_mode="api_key",
    ),
}

DEFAULT_PROVIDER = "claude-code"


def get_provider(provider_id: str) -> Provider | None:
    return PROVIDERS.get(provider_id)


def list_providers() -> list[dict]:
    """Return serializable list of providers for the frontend."""
    return [
        {
            "id": p.id,
            "name": p.name,
            "models": p.models,
            "default_model": p.default_model,
            "auth_modes": p.auth_modes,
            "default_auth_mode": p.default_auth_mode,
        }
        for p in PROVIDERS.values()
    ]
