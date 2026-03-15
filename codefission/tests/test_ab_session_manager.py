"""Tests for SessionManager."""

from pathlib import Path

import pytest

from agentbridge.discovery import AuthInfo, ProviderInfo
from agentbridge.session_manager import SessionManager, SwitchResult
from agentbridge.types import PermissionLevel, ProviderType


def _make_providers() -> list[ProviderInfo]:
    """Create test provider fixtures."""
    return [
        ProviderInfo(
            id="claude-code",
            name="Claude Code",
            installed=True,
            cli_path="/usr/bin/claude",
            version="2.1.74",
            auth=[AuthInfo(method="cli_oauth", authenticated=True, detail="user@test.com")],
            available_models=["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001"],
            default_model="claude-sonnet-4-6",
        ),
        ProviderInfo(
            id="codex",
            name="Codex CLI",
            installed=True,
            cli_path="/usr/bin/codex",
            version="0.101.0",
            auth=[AuthInfo(method="api_key", authenticated=True, detail="sk-abc...")],
            available_models=["o4-mini", "codex-mini", "gpt-5.3-codex"],
            default_model="gpt-5.3-codex",
        ),
        ProviderInfo(
            id="not-ready",
            name="Not Ready",
            installed=True,
            cli_path="/usr/bin/notready",
            version="1.0.0",
            auth=[AuthInfo(method="none", authenticated=False, detail="Not logged in")],
            available_models=["model-a"],
            default_model="model-a",
        ),
    ]


class TestSessionManagerInit:
    def test_no_initial_selection(self):
        mgr = SessionManager(_make_providers())
        assert mgr.current_provider is None
        assert mgr.current_provider_id is None
        assert mgr.current_model is None
        assert mgr.effective_model == ""

    def test_providers_listed(self):
        mgr = SessionManager(_make_providers())
        assert len(mgr.providers) == 3

    def test_ready_providers(self):
        mgr = SessionManager(_make_providers())
        ready = mgr.ready_providers
        assert len(ready) == 2
        assert all(p.ready for p in ready)


class TestSwitchProvider:
    def test_switch_to_ready_provider(self):
        mgr = SessionManager(_make_providers())
        result = mgr.switch_provider("claude-code")
        assert result.success is True
        assert result.provider_id == "claude-code"
        assert result.model == "claude-sonnet-4-6"
        assert mgr.current_provider_id == "claude-code"
        assert mgr.effective_model == "claude-sonnet-4-6"

    def test_switch_resets_model(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        mgr.switch_model("claude-opus-4-6")
        assert mgr.effective_model == "claude-opus-4-6"

        result = mgr.switch_provider("codex")
        assert result.success is True
        assert result.model == "gpt-5.3-codex"  # codex default
        assert mgr.current_model is None  # explicit selection cleared

    def test_switch_to_same_provider(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        result = mgr.switch_provider("claude-code")
        assert result.success is True
        assert "Already" in result.message

    def test_switch_to_unknown_provider(self):
        mgr = SessionManager(_make_providers())
        result = mgr.switch_provider("nonexistent")
        assert result.success is False
        assert "Unknown" in result.message

    def test_switch_to_not_ready_provider(self):
        mgr = SessionManager(_make_providers())
        result = mgr.switch_provider("not-ready")
        assert result.success is False
        assert "not ready" in result.message

    def test_switch_to_provider_without_adapter(self):
        providers = _make_providers()
        # Add a provider that has no adapter mapping
        providers.append(ProviderInfo(
            id="unknown-adapter",
            name="Unknown",
            installed=True,
            auth=[AuthInfo(method="api_key", authenticated=True)],
            available_models=["m1"],
            default_model="m1",
        ))
        mgr = SessionManager(providers)
        result = mgr.switch_provider("unknown-adapter")
        assert result.success is False
        assert "No adapter" in result.message


class TestSwitchModel:
    def test_switch_model(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        result = mgr.switch_model("claude-opus-4-6")
        assert result.success is True
        assert result.model == "claude-opus-4-6"
        assert mgr.current_model == "claude-opus-4-6"
        assert mgr.effective_model == "claude-opus-4-6"

    def test_switch_to_same_model(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        mgr.switch_model("claude-opus-4-6")
        result = mgr.switch_model("claude-opus-4-6")
        assert result.success is True
        assert "Already" in result.message

    def test_switch_to_default_is_noop(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        # Default is claude-sonnet-4-6, switching to it is a noop
        result = mgr.switch_model("claude-sonnet-4-6")
        assert result.success is True
        assert "Already" in result.message

    def test_switch_to_unknown_model(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        result = mgr.switch_model("nonexistent-model")
        assert result.success is False
        assert "Unknown model" in result.message
        assert "claude-sonnet-4-6" in result.message  # lists available

    def test_switch_model_no_provider(self):
        mgr = SessionManager(_make_providers())
        result = mgr.switch_model("anything")
        assert result.success is False
        assert "No provider" in result.message


class TestAvailableModels:
    def test_with_provider(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("codex")
        assert mgr.available_models == ["o4-mini", "codex-mini", "gpt-5.3-codex"]

    def test_without_provider(self):
        mgr = SessionManager(_make_providers())
        assert mgr.available_models == []


class TestApplySettings:
    def test_apply_both(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        result = mgr.apply_settings(provider="codex", model="o4-mini")
        assert result.success is True
        assert mgr.current_provider_id == "codex"
        assert mgr.effective_model == "o4-mini"

    def test_apply_provider_only(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        result = mgr.apply_settings(provider="codex")
        assert result.success is True
        assert mgr.current_provider_id == "codex"
        assert mgr.effective_model == "gpt-5.3-codex"

    def test_apply_model_only(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        result = mgr.apply_settings(model="claude-opus-4-6")
        assert result.success is True
        assert mgr.current_provider_id == "claude-code"
        assert mgr.effective_model == "claude-opus-4-6"

    def test_apply_empty_is_noop(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        result = mgr.apply_settings()
        assert result.success is True
        assert "No changes" in result.message
        assert mgr.current_provider_id == "claude-code"

    def test_apply_same_values_noop(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        result = mgr.apply_settings(provider="claude-code", model="claude-sonnet-4-6")
        assert result.success is True

    def test_apply_bad_provider_fails(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        result = mgr.apply_settings(provider="nonexistent")
        assert result.success is False
        # Should not change current provider
        assert mgr.current_provider_id == "claude-code"

    def test_apply_bad_model_fails(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        result = mgr.apply_settings(model="nonexistent")
        assert result.success is False
        assert mgr.effective_model == "claude-sonnet-4-6"


class TestBuildConfig:
    def test_basic_config(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        config = mgr.build_config(prompt="hello")
        assert config.provider == ProviderType.CLAUDE
        assert config.prompt == "hello"
        assert config.model is None  # None = use adapter default

    def test_with_explicit_model(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("codex")
        mgr.switch_model("o4-mini")
        config = mgr.build_config(prompt="test")
        assert config.provider == ProviderType.CODEX
        assert config.model == "o4-mini"

    def test_with_overrides(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        config = mgr.build_config(
            prompt="do stuff",
            cwd=Path("/my/project"),
            system_prompt="Be brief",
            max_turns=5,
            prior_context="previous work",
            permission_level=PermissionLevel.AUTONOMOUS,
            env={"KEY": "val"},
            extra_args=["--flag"],
        )
        assert config.cwd == Path("/my/project")
        assert config.system_prompt == "Be brief"
        assert config.max_turns == 5
        assert config.prior_context == "previous work"
        assert config.resume_session_id is None
        assert config.fork_session is False
        assert config.permission_level == PermissionLevel.AUTONOMOUS
        assert config.env == {"KEY": "val"}
        assert config.extra_args == ["--flag"]

    def test_build_config_with_custom_permission(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        config = mgr.build_config(
            prompt="test",
            permission_level=PermissionLevel.CUSTOM,
            permission_mode="dontAsk",
        )
        assert config.permission_level == PermissionLevel.CUSTOM
        assert config.permission_mode == "dontAsk"

    def test_no_provider_raises(self):
        mgr = SessionManager(_make_providers())
        with pytest.raises(RuntimeError, match="No provider selected"):
            mgr.build_config(prompt="hello")


class TestProviderType:
    def test_claude_type(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("claude-code")
        assert mgr.current_provider_type == ProviderType.CLAUDE

    def test_codex_type(self):
        mgr = SessionManager(_make_providers())
        mgr.switch_provider("codex")
        assert mgr.current_provider_type == ProviderType.CODEX

    def test_no_selection(self):
        mgr = SessionManager(_make_providers())
        assert mgr.current_provider_type is None
