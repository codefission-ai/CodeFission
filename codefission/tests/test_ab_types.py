"""Tests for types and config."""

from pathlib import Path

import pytest

from agentbridge.types import (
    PermissionLevel,
    ProviderType,
    SessionConfig,
    resolve_permission,
)


class TestProviderType:
    def test_values(self):
        assert ProviderType.CLAUDE.value == "claude-code"
        assert ProviderType.CODEX.value == "codex"

    def test_no_gemini(self):
        values = [p.value for p in ProviderType]
        assert "gemini" not in values

    def test_is_str_enum(self):
        assert isinstance(ProviderType.CLAUDE, str)
        assert ProviderType.CLAUDE == "claude-code"


class TestPermissionLevel:
    def test_values(self):
        assert PermissionLevel.AUTONOMOUS.value == "autonomous"
        assert PermissionLevel.AUTO_EDIT.value == "auto-edit"
        assert PermissionLevel.INTERACTIVE.value == "interactive"
        assert PermissionLevel.CUSTOM.value == "custom"

    def test_is_str_enum(self):
        assert isinstance(PermissionLevel.AUTONOMOUS, str)
        assert PermissionLevel.AUTONOMOUS == "autonomous"


class TestSessionConfig:
    def test_defaults(self):
        c = SessionConfig(provider=ProviderType.CLAUDE, prompt="hello")
        assert c.model is None
        assert c.system_prompt is None
        assert c.env == {}
        assert c.resume_session_id is None
        assert c.fork_session is False
        assert c.permission_level is None
        assert c.permission_mode is None
        assert c.sandbox_mode is None
        assert c.prior_context is None
        assert c.extra_args == []

    def test_cwd_defaults_to_current(self):
        c = SessionConfig(provider=ProviderType.CODEX, prompt="test")
        assert isinstance(c.cwd, Path)

    def test_all_fields_without_resume(self):
        c = SessionConfig(
            provider=ProviderType.CODEX,
            prompt="do stuff",
            cwd=Path("/tmp"),
            model="o4-mini",
            system_prompt="Be helpful",
            env={"FOO": "bar"},
            permission_level=PermissionLevel.CUSTOM,
            sandbox_mode="workspace-write",
            prior_context="previous context here",
            extra_args=["--flag"],
        )
        assert c.provider == ProviderType.CODEX
        assert c.permission_level == PermissionLevel.CUSTOM
        assert c.sandbox_mode == "workspace-write"
        assert c.prior_context == "previous context here"
        assert c.extra_args == ["--flag"]

    def test_resume_without_system_prompt_ok(self):
        c = SessionConfig(
            provider=ProviderType.CLAUDE,
            prompt="continue",
            resume_session_id="sess-1",
            fork_session=True,
        )
        assert c.resume_session_id == "sess-1"
        assert c.system_prompt is None

    def test_resume_with_system_prompt_raises(self):
        with pytest.raises(ValueError, match="system_prompt cannot be changed when resuming"):
            SessionConfig(
                provider=ProviderType.CLAUDE,
                prompt="continue",
                system_prompt="new instructions",
                resume_session_id="sess-1",
            )


class TestPermissionLevelValidation:
    def test_unified_level_without_provider_fields_ok(self):
        c = SessionConfig(
            provider=ProviderType.CLAUDE,
            prompt="test",
            permission_level=PermissionLevel.AUTONOMOUS,
        )
        assert c.permission_level == PermissionLevel.AUTONOMOUS

    def test_unified_level_with_permission_mode_raises(self):
        with pytest.raises(ValueError, match="do not set permission_mode or sandbox_mode"):
            SessionConfig(
                provider=ProviderType.CLAUDE,
                prompt="test",
                permission_level=PermissionLevel.AUTONOMOUS,
                permission_mode="bypassPermissions",
            )

    def test_unified_level_with_sandbox_mode_raises(self):
        with pytest.raises(ValueError, match="do not set permission_mode or sandbox_mode"):
            SessionConfig(
                provider=ProviderType.CODEX,
                prompt="test",
                permission_level=PermissionLevel.AUTO_EDIT,
                sandbox_mode="workspace-write",
            )

    def test_custom_with_provider_fields_ok(self):
        c = SessionConfig(
            provider=ProviderType.CLAUDE,
            prompt="test",
            permission_level=PermissionLevel.CUSTOM,
            permission_mode="dontAsk",
        )
        assert c.permission_mode == "dontAsk"

    def test_no_level_with_provider_fields_ok(self):
        c = SessionConfig(
            provider=ProviderType.CLAUDE,
            prompt="test",
            permission_mode="plan",
        )
        assert c.permission_mode == "plan"


class TestResolvePermission:
    def test_autonomous_claude(self):
        c = SessionConfig(
            provider=ProviderType.CLAUDE, prompt="test",
            permission_level=PermissionLevel.AUTONOMOUS,
        )
        assert resolve_permission(c) == "bypassPermissions"

    def test_autonomous_codex(self):
        c = SessionConfig(
            provider=ProviderType.CODEX, prompt="test",
            permission_level=PermissionLevel.AUTONOMOUS,
        )
        assert resolve_permission(c) == "full-auto"

    def test_auto_edit_claude(self):
        c = SessionConfig(
            provider=ProviderType.CLAUDE, prompt="test",
            permission_level=PermissionLevel.AUTO_EDIT,
        )
        assert resolve_permission(c) == "acceptEdits"

    def test_auto_edit_codex(self):
        c = SessionConfig(
            provider=ProviderType.CODEX, prompt="test",
            permission_level=PermissionLevel.AUTO_EDIT,
        )
        assert resolve_permission(c) == "auto-edit"

    def test_interactive_claude(self):
        c = SessionConfig(
            provider=ProviderType.CLAUDE, prompt="test",
            permission_level=PermissionLevel.INTERACTIVE,
        )
        assert resolve_permission(c) == "default"

    def test_interactive_codex(self):
        c = SessionConfig(
            provider=ProviderType.CODEX, prompt="test",
            permission_level=PermissionLevel.INTERACTIVE,
        )
        assert resolve_permission(c) == "suggest"

    def test_custom_falls_through_to_claude_field(self):
        c = SessionConfig(
            provider=ProviderType.CLAUDE, prompt="test",
            permission_level=PermissionLevel.CUSTOM,
            permission_mode="dontAsk",
        )
        assert resolve_permission(c) == "dontAsk"

    def test_custom_falls_through_to_codex_field(self):
        c = SessionConfig(
            provider=ProviderType.CODEX, prompt="test",
            permission_level=PermissionLevel.CUSTOM,
            sandbox_mode="workspace-write",
        )
        assert resolve_permission(c) == "workspace-write"

    def test_no_level_falls_through(self):
        c = SessionConfig(
            provider=ProviderType.CLAUDE, prompt="test",
            permission_mode="plan",
        )
        assert resolve_permission(c) == "plan"

    def test_no_level_no_field_returns_none(self):
        c = SessionConfig(provider=ProviderType.CLAUDE, prompt="test")
        assert resolve_permission(c) is None
