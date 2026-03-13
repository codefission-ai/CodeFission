"""Tests for types and config."""

from pathlib import Path

from agentbridge.types import ProviderType, SessionConfig


class TestProviderType:
    def test_values(self):
        assert ProviderType.CLAUDE.value == "claude"
        assert ProviderType.CODEX.value == "codex"

    def test_no_gemini(self):
        values = [p.value for p in ProviderType]
        assert "gemini" not in values

    def test_is_str_enum(self):
        assert isinstance(ProviderType.CLAUDE, str)
        assert ProviderType.CLAUDE == "claude"


class TestSessionConfig:
    def test_defaults(self):
        c = SessionConfig(provider=ProviderType.CLAUDE, prompt="hello")
        assert c.model is None
        assert c.system_prompt is None
        assert c.env == {}
        assert c.max_turns is None
        assert c.resume_session_id is None
        assert c.fork_session is False
        assert c.permission_mode is None
        assert c.sandbox_mode is None
        assert c.prior_context is None
        assert c.extra_args == []

    def test_cwd_defaults_to_current(self):
        c = SessionConfig(provider=ProviderType.CODEX, prompt="test")
        assert isinstance(c.cwd, Path)

    def test_all_fields(self):
        c = SessionConfig(
            provider=ProviderType.CODEX,
            prompt="do stuff",
            cwd=Path("/tmp"),
            model="o4-mini",
            system_prompt="Be helpful",
            env={"FOO": "bar"},
            max_turns=5,
            resume_session_id="sess-1",
            fork_session=True,
            permission_mode="bypassPermissions",
            sandbox_mode="workspace-write",
            prior_context="previous context here",
            extra_args=["--flag"],
        )
        assert c.provider == ProviderType.CODEX
        assert c.prior_context == "previous context here"
        assert c.extra_args == ["--flag"]
