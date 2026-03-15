"""Shared types and configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ProviderType(str, Enum):
    CLAUDE = "claude-code"
    CODEX = "codex"


class PermissionLevel(str, Enum):
    """Unified permission level that maps to each provider's native mode.

    ┌──────────────┬────────────────────────┬──────────────────┐
    │ Level        │ Claude (--permission-  │ Codex (--approval│
    │              │  mode)                 │ -mode)           │
    ├──────────────┼────────────────────────┼──────────────────┤
    │ AUTONOMOUS   │ bypassPermissions      │ full-auto        │
    │ AUTO_EDIT    │ acceptEdits            │ auto-edit        │
    │ INTERACTIVE  │ default                │ suggest          │
    │ CUSTOM       │ uses permission_mode   │ uses sandbox_mode│
    └──────────────┴────────────────────────┴──────────────────┘
    """
    AUTONOMOUS = "autonomous"
    AUTO_EDIT = "auto-edit"
    INTERACTIVE = "interactive"
    CUSTOM = "custom"


# Maps unified level → native permission string per provider
_PERMISSION_MAP: dict[PermissionLevel, dict[ProviderType, str]] = {
    PermissionLevel.AUTONOMOUS: {
        ProviderType.CLAUDE: "bypassPermissions",
        ProviderType.CODEX: "full-auto",
    },
    PermissionLevel.AUTO_EDIT: {
        ProviderType.CLAUDE: "acceptEdits",
        ProviderType.CODEX: "auto-edit",
    },
    PermissionLevel.INTERACTIVE: {
        ProviderType.CLAUDE: "default",
        ProviderType.CODEX: "suggest",
    },
}


def resolve_permission(config: SessionConfig) -> str | None:
    """Resolve the effective native permission string for a config's provider.

    Returns the native string (e.g. "bypassPermissions", "full-auto") or None
    if no permission level or provider-specific override is set.
    """
    if config.permission_level and config.permission_level != PermissionLevel.CUSTOM:
        mapping = _PERMISSION_MAP.get(config.permission_level, {})
        return mapping.get(config.provider)

    # CUSTOM or unset — fall through to provider-specific fields
    if config.provider == ProviderType.CLAUDE:
        return config.permission_mode
    if config.provider == ProviderType.CODEX:
        return config.sandbox_mode
    return None


@dataclass
class SessionConfig:
    """Configuration for a single agent session."""

    provider: ProviderType
    prompt: str
    cwd: Path = field(default_factory=Path.cwd)
    model: str | None = None
    system_prompt: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    # Session resume / fork
    resume_session_id: str | None = None
    fork_session: bool = False

    # Unified permission level (preferred)
    permission_level: PermissionLevel | None = None

    # Provider-specific overrides (used when permission_level is CUSTOM or unset)
    permission_mode: str | None = None     # Claude-specific: "bypassPermissions", "plan", "dontAsk", etc.
    sandbox_mode: str | None = None        # Codex-specific: "workspace-write", etc.

    # Cross-provider context transfer
    prior_context: str | None = None       # Formatted history from another provider

    # Escape hatch for arbitrary CLI flags
    extra_args: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.resume_session_id and self.system_prompt is not None:
            raise ValueError(
                "system_prompt cannot be changed when resuming/forking a session "
                f"(resume_session_id={self.resume_session_id!r}). "
                "The resumed session retains its original system prompt. "
                "Prepend instructions to the prompt field instead."
            )

        if (
            self.permission_level is not None
            and self.permission_level != PermissionLevel.CUSTOM
            and (self.permission_mode is not None or self.sandbox_mode is not None)
        ):
            raise ValueError(
                f"permission_level={self.permission_level.value!r} is a unified level — "
                "do not set permission_mode or sandbox_mode alongside it. "
                "Use permission_level=PermissionLevel.CUSTOM to pass provider-specific values."
            )
