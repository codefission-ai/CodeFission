"""Tests for Claude and Codex adapters with mocked subprocess output."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentbridge.adapters.claude import ClaudeAdapter
from agentbridge.adapters.codex import CodexAdapter
from agentbridge.adapters import get_adapter
from agentbridge.events import (
    SessionInit,
    TextDelta,
    ToolEnd,
    ToolStart,
    TurnComplete,
)
from agentbridge.types import PermissionLevel, ProviderType, SessionConfig


# ── Helpers ────────────────────────────────────────────────────────────


def _make_config(provider: ProviderType, **kwargs) -> SessionConfig:
    defaults = dict(provider=provider, prompt="test prompt", cwd=Path("/tmp"))
    defaults.update(kwargs)
    return SessionConfig(**defaults)


class FakeRunner:
    """Mock SubprocessRunner that yields pre-defined JSONL events."""

    def __init__(self, events: list[dict]):
        self._events = events
        self.pid = 12345
        self.stdin_closed = False

    async def read_events(self):
        for event in self._events:
            yield event

    async def close_stdin(self):
        self.stdin_closed = True

    async def close(self):
        pass


async def _collect_events(adapter, runner, config):
    events = []
    async for event in adapter.stream(runner, config):
        events.append(event)
    return events


# ── Claude Adapter ─────────────────────────────────────────────────────


class TestClaudeAdapterBuildCommand:
    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_basic_command(self, _):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE)
        cmd = adapter.build_command(config)
        assert cmd[0] == "/usr/bin/claude"
        assert "-p" in cmd
        assert "test prompt" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--verbose" in cmd

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_model_flag(self, _):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE, model="claude-opus-4-6")
        cmd = adapter.build_command(config)
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-opus-4-6"

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_resume_and_fork(self, _):
        adapter = ClaudeAdapter()
        config = _make_config(
            ProviderType.CLAUDE,
            resume_session_id="sess-123",
            fork_session=True,
        )
        cmd = adapter.build_command(config)
        assert "--resume" in cmd
        assert "sess-123" in cmd
        assert "--fork-session" in cmd

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_prior_context_prepended(self, _):
        adapter = ClaudeAdapter()
        config = _make_config(
            ProviderType.CLAUDE,
            prior_context="Previous conversation summary",
        )
        cmd = adapter.build_command(config)
        idx = cmd.index("-p")
        prompt = cmd[idx + 1]
        assert prompt.startswith("Previous conversation summary")
        assert "test prompt" in prompt

    @patch("shutil.which", return_value=None)
    def test_missing_cli_raises(self, _):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE)
        with pytest.raises(FileNotFoundError, match="claude CLI not found"):
            adapter.build_command(config)

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_system_prompt(self, _):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE, system_prompt="Be concise")
        cmd = adapter.build_command(config)
        idx = cmd.index("--system-prompt")
        assert cmd[idx + 1] == "Be concise"

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_default_permission_mode(self, _):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE)
        cmd = adapter.build_command(config)
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "bypassPermissions"

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_permission_level_autonomous(self, _):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE, permission_level=PermissionLevel.AUTONOMOUS)
        cmd = adapter.build_command(config)
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "bypassPermissions"

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_permission_level_interactive(self, _):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE, permission_level=PermissionLevel.INTERACTIVE)
        cmd = adapter.build_command(config)
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "default"

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_permission_level_custom(self, _):
        adapter = ClaudeAdapter()
        config = _make_config(
            ProviderType.CLAUDE,
            permission_level=PermissionLevel.CUSTOM,
            permission_mode="dontAsk",
        )
        cmd = adapter.build_command(config)
        idx = cmd.index("--permission-mode")
        assert cmd[idx + 1] == "dontAsk"

    @patch("shutil.which", return_value="/usr/bin/claude")
    def test_extra_args(self, _):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE, extra_args=["--flag", "value"])
        cmd = adapter.build_command(config)
        assert "--flag" in cmd
        assert "value" in cmd


class TestClaudeAdapterBuildEnv:
    def test_claudecode_unset(self):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE)
        env = adapter.build_env(config)
        assert env["CLAUDECODE"] == ""

    def test_user_env_merged(self):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE, env={"MY_VAR": "123"})
        env = adapter.build_env(config)
        assert env["MY_VAR"] == "123"
        assert env["CLAUDECODE"] == ""


class TestClaudeAdapterStream:
    @pytest.mark.asyncio
    async def test_text_streaming(self):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE)
        events = [
            {
                "type": "stream_event",
                "session_id": "s1",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Hello"},
                },
            },
            {
                "type": "stream_event",
                "session_id": "s1",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": " world"},
                },
            },
            {
                "type": "result",
                "session_id": "s1",
                "is_error": False,
                "duration_ms": 500,
                "total_cost_usd": 0.01,
                "num_turns": 1,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 10,
                },
            },
        ]
        runner = FakeRunner(events)
        result = await _collect_events(adapter, runner, config)

        assert runner.stdin_closed
        # SessionInit + 2 TextDelta + TurnComplete
        session_inits = [e for e in result if isinstance(e, SessionInit)]
        text_deltas = [e for e in result if isinstance(e, TextDelta)]
        turn_completes = [e for e in result if isinstance(e, TurnComplete)]

        assert len(session_inits) == 1
        assert session_inits[0].session_id == "s1"
        assert len(text_deltas) == 2
        assert text_deltas[0].text == "Hello"
        assert text_deltas[1].text == " world"
        assert len(turn_completes) == 1
        assert turn_completes[0].cost_usd == 0.01
        assert turn_completes[0].duration_ms == 500
        assert turn_completes[0].token_usage is not None
        assert turn_completes[0].token_usage["input_tokens"] == 100
        assert turn_completes[0].token_usage["cached_input_tokens"] == 10

    @pytest.mark.asyncio
    async def test_tool_use(self):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE)
        events = [
            {
                "type": "stream_event",
                "session_id": "s1",
                "event": {
                    "type": "content_block_start",
                    "content_block": {
                        "type": "tool_use",
                        "id": "tc1",
                        "name": "Bash",
                    },
                },
            },
            {
                "type": "stream_event",
                "session_id": "s1",
                "event": {
                    "type": "content_block_delta",
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"command": "ls"}',
                    },
                },
            },
            {
                "type": "stream_event",
                "session_id": "s1",
                "event": {"type": "content_block_stop"},
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tc1",
                            "content": "file1.txt\nfile2.txt",
                            "is_error": False,
                        }
                    ]
                },
            },
            {
                "type": "result",
                "session_id": "s1",
                "is_error": False,
            },
        ]
        runner = FakeRunner(events)
        result = await _collect_events(adapter, runner, config)

        tool_starts = [e for e in result if isinstance(e, ToolStart)]
        tool_ends = [e for e in result if isinstance(e, ToolEnd)]

        # content_block_start emits one ToolStart, content_block_stop emits another with args
        assert len(tool_starts) == 2
        assert tool_starts[0].name == "Bash"
        assert tool_starts[1].arguments == {"command": "ls"}
        assert len(tool_ends) == 1
        assert tool_ends[0].result == "file1.txt\nfile2.txt"
        assert tool_ends[0].is_error is False

    @pytest.mark.asyncio
    async def test_error_result(self):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE)
        events = [
            {
                "type": "result",
                "session_id": "s1",
                "is_error": True,
            },
        ]
        runner = FakeRunner(events)
        result = await _collect_events(adapter, runner, config)

        turn_completes = [e for e in result if isinstance(e, TurnComplete)]
        assert len(turn_completes) == 1
        assert turn_completes[0].is_error is True

    @pytest.mark.asyncio
    async def test_result_without_usage(self):
        adapter = ClaudeAdapter()
        config = _make_config(ProviderType.CLAUDE)
        events = [
            {"type": "result", "session_id": "s1", "is_error": False},
        ]
        runner = FakeRunner(events)
        result = await _collect_events(adapter, runner, config)
        tc = [e for e in result if isinstance(e, TurnComplete)][0]
        assert tc.token_usage is None


# ── Codex Adapter ──────────────────────────────────────────────────────


class TestCodexAdapterBuildCommand:
    @patch("shutil.which", return_value="/usr/bin/codex")
    def test_basic_command(self, _):
        adapter = CodexAdapter()
        config = _make_config(ProviderType.CODEX)
        cmd = adapter.build_command(config)
        assert cmd[0] == "/usr/bin/codex"
        assert "exec" in cmd
        assert "--json" in cmd
        assert "--full-auto" in cmd
        assert "test prompt" in cmd

    @patch("shutil.which", return_value="/usr/bin/codex")
    def test_model_flag(self, _):
        adapter = CodexAdapter()
        config = _make_config(ProviderType.CODEX, model="o4-mini")
        cmd = adapter.build_command(config)
        idx = cmd.index("-m")
        assert cmd[idx + 1] == "o4-mini"

    @patch("shutil.which", return_value="/usr/bin/codex")
    def test_cwd_flag(self, _):
        adapter = CodexAdapter()
        config = _make_config(ProviderType.CODEX, cwd=Path("/my/project"))
        cmd = adapter.build_command(config)
        idx = cmd.index("-C")
        assert cmd[idx + 1] == "/my/project"

    @patch("shutil.which", return_value="/usr/bin/codex")
    def test_sandbox_mode(self, _):
        adapter = CodexAdapter()
        config = _make_config(
            ProviderType.CODEX,
            permission_level=PermissionLevel.CUSTOM,
            sandbox_mode="workspace-write",
        )
        cmd = adapter.build_command(config)
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "workspace-write"

    @patch("shutil.which", return_value="/usr/bin/codex")
    def test_permission_level_autonomous(self, _):
        adapter = CodexAdapter()
        config = _make_config(ProviderType.CODEX, permission_level=PermissionLevel.AUTONOMOUS)
        cmd = adapter.build_command(config)
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "full-auto"

    @patch("shutil.which", return_value="/usr/bin/codex")
    def test_permission_level_interactive(self, _):
        adapter = CodexAdapter()
        config = _make_config(ProviderType.CODEX, permission_level=PermissionLevel.INTERACTIVE)
        cmd = adapter.build_command(config)
        idx = cmd.index("--sandbox")
        assert cmd[idx + 1] == "suggest"

    @patch("shutil.which", return_value="/usr/bin/codex")
    def test_prior_context_prepended(self, _):
        adapter = CodexAdapter()
        config = _make_config(
            ProviderType.CODEX,
            prior_context="Previous context",
        )
        cmd = adapter.build_command(config)
        # Prompt is the last element
        prompt = cmd[-1]
        assert prompt.startswith("Previous context")
        assert "test prompt" in prompt

    @patch("shutil.which", return_value="/usr/bin/codex")
    def test_resume_session(self, _):
        adapter = CodexAdapter()
        config = _make_config(
            ProviderType.CODEX,
            resume_session_id="thread-abc",
        )
        cmd = adapter.build_command(config)
        assert "resume" in cmd
        assert "thread-abc" in cmd

    @patch("shutil.which", return_value=None)
    def test_missing_cli_raises(self, _):
        adapter = CodexAdapter()
        config = _make_config(ProviderType.CODEX)
        with pytest.raises(FileNotFoundError, match="codex CLI not found"):
            adapter.build_command(config)


class TestCodexAdapterStream:
    @pytest.mark.asyncio
    async def test_full_turn(self):
        adapter = CodexAdapter()
        config = _make_config(ProviderType.CODEX, model="o4-mini")
        events = [
            {"type": "thread.started", "thread_id": "t1"},
            {
                "type": "item.completed",
                "item": {
                    "id": "msg1",
                    "type": "agent_message",
                    "text": "Hello from Codex!",
                },
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cached_input_tokens": 0,
                },
            },
        ]
        runner = FakeRunner(events)
        result = await _collect_events(adapter, runner, config)

        session_inits = [e for e in result if isinstance(e, SessionInit)]
        text_deltas = [e for e in result if isinstance(e, TextDelta)]
        turn_completes = [e for e in result if isinstance(e, TurnComplete)]

        assert len(session_inits) == 1
        assert session_inits[0].session_id == "t1"
        assert len(text_deltas) == 1
        assert text_deltas[0].text == "Hello from Codex!"
        assert len(turn_completes) == 1
        assert turn_completes[0].is_error is False
        assert turn_completes[0].token_usage is not None
        assert turn_completes[0].cost_usd is not None

    @pytest.mark.asyncio
    async def test_command_execution(self):
        adapter = CodexAdapter()
        config = _make_config(ProviderType.CODEX)
        events = [
            {"type": "thread.started", "thread_id": "t1"},
            {
                "type": "item.started",
                "item": {
                    "id": "cmd1",
                    "type": "command_execution",
                    "command": "ls -la",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "cmd1",
                    "type": "command_execution",
                    "aggregated_output": "total 8\ndrwxr-xr-x 2 user group 64 Jan 1 00:00 .",
                    "exit_code": 0,
                },
            },
            {"type": "turn.completed"},
        ]
        runner = FakeRunner(events)
        result = await _collect_events(adapter, runner, config)

        tool_starts = [e for e in result if isinstance(e, ToolStart)]
        tool_ends = [e for e in result if isinstance(e, ToolEnd)]

        assert len(tool_starts) == 1
        assert tool_starts[0].name == "bash"
        assert tool_starts[0].arguments == {"command": "ls -la"}
        assert len(tool_ends) == 1
        assert tool_ends[0].is_error is False
        assert "total 8" in tool_ends[0].result

    @pytest.mark.asyncio
    async def test_command_error(self):
        adapter = CodexAdapter()
        config = _make_config(ProviderType.CODEX)
        events = [
            {"type": "thread.started", "thread_id": "t1"},
            {
                "type": "item.started",
                "item": {"id": "cmd1", "type": "command_execution", "command": "false"},
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "cmd1",
                    "type": "command_execution",
                    "aggregated_output": "",
                    "exit_code": 1,
                },
            },
            {"type": "turn.completed"},
        ]
        runner = FakeRunner(events)
        result = await _collect_events(adapter, runner, config)

        tool_ends = [e for e in result if isinstance(e, ToolEnd)]
        assert tool_ends[0].is_error is True

    @pytest.mark.asyncio
    async def test_mcp_tool_call(self):
        adapter = CodexAdapter()
        config = _make_config(ProviderType.CODEX)
        events = [
            {"type": "thread.started", "thread_id": "t1"},
            {
                "type": "item.started",
                "item": {
                    "id": "mcp1",
                    "type": "mcp_tool_call",
                    "tool": "read_file",
                    "arguments": {"path": "/tmp/test.txt"},
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "mcp1",
                    "type": "mcp_tool_call",
                    "result": {"content": "file contents here"},
                },
            },
            {"type": "turn.completed"},
        ]
        runner = FakeRunner(events)
        result = await _collect_events(adapter, runner, config)

        tool_starts = [e for e in result if isinstance(e, ToolStart)]
        tool_ends = [e for e in result if isinstance(e, ToolEnd)]

        assert len(tool_starts) == 1
        assert tool_starts[0].name == "read_file"
        assert len(tool_ends) == 1
        assert "file contents here" in tool_ends[0].result

    @pytest.mark.asyncio
    async def test_file_change(self):
        adapter = CodexAdapter()
        config = _make_config(ProviderType.CODEX)
        events = [
            {"type": "thread.started", "thread_id": "t1"},
            {
                "type": "item.started",
                "item": {
                    "id": "fc1",
                    "type": "file_change",
                    "changes": [{"kind": "edit", "path": "main.py"}],
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "fc1",
                    "type": "file_change",
                    "changes": [{"kind": "edit", "path": "main.py"}],
                },
            },
            {"type": "turn.completed"},
        ]
        runner = FakeRunner(events)
        result = await _collect_events(adapter, runner, config)

        tool_starts = [e for e in result if isinstance(e, ToolStart)]
        tool_ends = [e for e in result if isinstance(e, ToolEnd)]

        assert tool_starts[0].name == "file_edit"
        assert "edit main.py" in tool_starts[0].arguments["changes"]
        assert tool_ends[0].name == "file_edit"

    @pytest.mark.asyncio
    async def test_turn_failed(self):
        adapter = CodexAdapter()
        config = _make_config(ProviderType.CODEX)
        events = [
            {"type": "thread.started", "thread_id": "t1"},
            {"type": "turn.failed"},
        ]
        runner = FakeRunner(events)
        result = await _collect_events(adapter, runner, config)

        turn_completes = [e for e in result if isinstance(e, TurnComplete)]
        assert len(turn_completes) == 1
        assert turn_completes[0].is_error is True

    @pytest.mark.asyncio
    async def test_error_event(self):
        adapter = CodexAdapter()
        config = _make_config(ProviderType.CODEX)
        events = [
            {"type": "thread.started", "thread_id": "t1"},
            {"type": "error", "message": "something went wrong"},
        ]
        runner = FakeRunner(events)
        result = await _collect_events(adapter, runner, config)

        turn_completes = [e for e in result if isinstance(e, TurnComplete)]
        assert len(turn_completes) == 1
        assert turn_completes[0].is_error is True


# ── Adapter Registry ──────────────────────────────────────────────────


class TestAdapterRegistry:
    def test_get_claude_adapter(self):
        adapter = get_adapter(ProviderType.CLAUDE)
        assert isinstance(adapter, ClaudeAdapter)

    def test_get_codex_adapter(self):
        adapter = get_adapter(ProviderType.CODEX)
        assert isinstance(adapter, CodexAdapter)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="No adapter"):
            get_adapter("nonexistent")  # type: ignore
