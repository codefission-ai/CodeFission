"""Shared types and configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ProviderType(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"


@dataclass
class SessionConfig:
    """Configuration for a single agent session."""

    provider: ProviderType
    prompt: str
    cwd: Path = field(default_factory=Path.cwd)
    model: str | None = None
    system_prompt: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    max_turns: int | None = None

    # Session resume / fork
    resume_session_id: str | None = None
    fork_session: bool = False

    # Provider-specific (optional, ignored if not applicable)
    permission_mode: str | None = None     # Claude: "bypassPermissions"
    sandbox_mode: str | None = None        # Codex: "workspace-write"

    # Cross-provider context transfer
    prior_context: str | None = None       # Formatted history from another provider

    # Escape hatch for arbitrary CLI flags
    extra_args: list[str] = field(default_factory=list)
