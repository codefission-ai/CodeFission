"""Stateful manager for provider/model selection.

Holds the current provider and model, validates switches, and builds
SessionConfig objects. Both the interactive CLI and the CodeFission app
use this instead of managing state ad-hoc.

Usage (CLI)::

    mgr = await SessionManager.create()
    mgr.switch_provider("codex")
    mgr.switch_model("o4-mini")
    config = mgr.build_config(prompt="hello")

Usage (CodeFission — per-tree settings)::

    mgr = await SessionManager.create()
    # Apply tree overrides (empty string = keep current)
    mgr.apply_settings(provider="codex", model="o4-mini")
    config = mgr.build_config(prompt=user_message, cwd=workspace)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .discovery import ProviderInfo, discover
from .types import PermissionLevel, ProviderType, SessionConfig

_PROVIDER_TYPE_MAP = {
    "claude-code": ProviderType.CLAUDE,
    "codex": ProviderType.CODEX,
}


@dataclass
class SwitchResult:
    """Outcome of a provider/model switch."""
    success: bool
    message: str
    provider_id: str   # current provider after the operation
    model: str         # current (effective) model after the operation


class SessionManager:
    """Manages current provider/model selection with validation.

    Holds discovered ProviderInfo objects and tracks the active provider
    and model. All mutations return a SwitchResult so callers (CLI, web
    handler, etc.) can report status without coupling to presentation.
    """

    def __init__(self, providers: list[ProviderInfo]) -> None:
        self._providers = providers
        self._providers_by_id: dict[str, ProviderInfo] = {p.id: p for p in providers}
        self._current_id: str | None = None
        self._current_model: str | None = None

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    async def create(cls, initial_provider: str | None = None) -> SessionManager:
        """Discover providers and return a ready manager.

        If initial_provider is given and ready, it becomes the active
        provider. Otherwise no provider is selected yet.
        """
        providers = await discover()
        mgr = cls(providers)
        if initial_provider:
            mgr.switch_provider(initial_provider)
        return mgr

    # ── Queries ──────────────────────────────────────────────────────

    @property
    def providers(self) -> list[ProviderInfo]:
        return list(self._providers)

    @property
    def ready_providers(self) -> list[ProviderInfo]:
        return [p for p in self._providers if p.ready]

    @property
    def current_provider(self) -> ProviderInfo | None:
        if self._current_id is None:
            return None
        return self._providers_by_id.get(self._current_id)

    @property
    def current_provider_id(self) -> str | None:
        return self._current_id

    @property
    def current_model(self) -> str | None:
        """Explicitly selected model, or None (= use provider default)."""
        return self._current_model

    @property
    def effective_model(self) -> str:
        """The model that will actually be used (explicit or default)."""
        if self._current_model:
            return self._current_model
        p = self.current_provider
        return p.default_model if p else ""

    @property
    def available_models(self) -> list[str]:
        p = self.current_provider
        return list(p.available_models) if p else []

    @property
    def current_provider_type(self) -> ProviderType | None:
        if self._current_id is None:
            return None
        return _PROVIDER_TYPE_MAP.get(self._current_id)

    # ── Mutations ────────────────────────────────────────────────────

    async def refresh(self) -> list[ProviderInfo]:
        """Re-run provider discovery and update internal state.

        If the current provider is no longer ready after refresh,
        it remains selected but callers should check .current_provider.ready.
        """
        self._providers = await discover()
        self._providers_by_id = {p.id: p for p in self._providers}
        return self._providers

    def switch_provider(self, provider_id: str) -> SwitchResult:
        """Switch to a different provider. Resets model to the new default."""
        target = self._providers_by_id.get(provider_id)
        if target is None:
            return SwitchResult(
                success=False,
                message=f"Unknown provider '{provider_id}'",
                provider_id=self._current_id or "",
                model=self.effective_model,
            )

        if not target.ready:
            return SwitchResult(
                success=False,
                message=f"{target.name} is not ready (missing install or auth)",
                provider_id=self._current_id or "",
                model=self.effective_model,
            )

        if provider_id not in _PROVIDER_TYPE_MAP:
            return SwitchResult(
                success=False,
                message=f"No adapter for provider '{provider_id}'",
                provider_id=self._current_id or "",
                model=self.effective_model,
            )

        if provider_id == self._current_id:
            return SwitchResult(
                success=True,
                message=f"Already using {target.name}",
                provider_id=provider_id,
                model=self.effective_model,
            )

        self._current_id = provider_id
        self._current_model = None  # reset to new provider's default
        return SwitchResult(
            success=True,
            message=f"Switched to {target.name}",
            provider_id=provider_id,
            model=self.effective_model,
        )

    def switch_model(self, model: str) -> SwitchResult:
        """Switch to a different model within the current provider."""
        p = self.current_provider
        if p is None:
            return SwitchResult(
                success=False,
                message="No provider selected",
                provider_id="",
                model="",
            )

        if model not in p.available_models:
            available = ", ".join(p.available_models)
            return SwitchResult(
                success=False,
                message=f"Unknown model '{model}' for {p.name}. Available: {available}",
                provider_id=self._current_id or "",
                model=self.effective_model,
            )

        if model == self.effective_model:
            return SwitchResult(
                success=True,
                message=f"Already using {model}",
                provider_id=self._current_id or "",
                model=model,
            )

        self._current_model = model
        return SwitchResult(
            success=True,
            message=f"Switched to model {model}",
            provider_id=self._current_id or "",
            model=model,
        )

    def apply_settings(
        self,
        provider: str = "",
        model: str = "",
    ) -> SwitchResult:
        """Apply provider/model settings (e.g. from CodeFission tree overrides).

        Empty string means "keep current" (or use default if nothing set).
        Returns the result of the last change, or a no-op result.
        """
        result = SwitchResult(
            success=True,
            message="No changes",
            provider_id=self._current_id or "",
            model=self.effective_model,
        )

        if provider and provider != self._current_id:
            result = self.switch_provider(provider)
            if not result.success:
                return result

        if model and model != self.effective_model:
            result = self.switch_model(model)

        return result

    # ── Config builder ───────────────────────────────────────────────

    def build_config(
        self,
        prompt: str,
        cwd: Path | None = None,
        *,
        system_prompt: str | None = None,
        prior_context: str | None = None,
        resume_session_id: str | None = None,
        fork_session: bool = False,
        permission_level: PermissionLevel | None = None,
        permission_mode: str | None = None,
        sandbox_mode: str | None = None,
        env: dict[str, str] | None = None,
        extra_args: list[str] | None = None,
        disable_global_memory: bool = False,
    ) -> SessionConfig:
        """Build a SessionConfig from current state + per-call overrides."""
        provider_type = self.current_provider_type
        if provider_type is None:
            raise RuntimeError("No provider selected — call switch_provider() first")

        return SessionConfig(
            provider=provider_type,
            prompt=prompt,
            cwd=cwd or Path.cwd(),
            model=self._current_model,
            system_prompt=system_prompt,
            prior_context=prior_context,
            resume_session_id=resume_session_id,
            fork_session=fork_session,
            permission_level=permission_level,
            permission_mode=permission_mode,
            sandbox_mode=sandbox_mode,
            env=env or {},
            extra_args=extra_args or [],
            disable_global_memory=disable_global_memory,
        )
