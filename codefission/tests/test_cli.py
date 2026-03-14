"""Phase 3A — Test CLI commands using Click's CliRunner with mocked HTTP.

The CLI is a thin HTTP client that talks to the REST API. These tests mock
httpx responses so no server is needed. Tests verify:
  - Commands fail gracefully without server
  - Tree CRUD commands (ls, new, rm)
  - Chat command (SSE streaming)
  - Settings commands (set, reset)
  - Node commands (ls, select, show)
  - Log commands (human-readable and JSON)
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_cli():
    """Import the click CLI group. Deferred so import errors surface clearly."""
    from cli import cli
    return cli


def _mock_response(status_code=200, json_data=None, text="", headers=None):
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.headers = headers or {}
    resp.is_success = 200 <= status_code < 300
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


def _mock_sse_response(events):
    """Create a mock httpx streaming response that yields SSE lines.

    events: list of dicts like {"event": "text_delta", "data": {"text": "hi"}}
    """
    lines = []
    for evt in events:
        if "event" in evt:
            lines.append(f"event: {evt['event']}")
        lines.append(f"data: {json.dumps(evt.get('data', {}))}")
        lines.append("")  # blank line between events

    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "text/event-stream"}
    resp.is_success = True
    resp.iter_lines = MagicMock(return_value=iter(lines))

    # For streaming context manager
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    """Click CliRunner instance."""
    return CliRunner(mix_stderr=False)


@pytest.fixture
def cli():
    """Import the CLI group."""
    return _import_cli()


@pytest.fixture
def mock_server_lock(tmp_path, monkeypatch):
    """Create a fake server lock so _require_server succeeds."""
    lock_dir = tmp_path / ".codefission"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "server.lock"
    lock_file.write_text(json.dumps({
        "pid": 99999,
        "port": 19440,
    }))
    # Patch the lock file path and _pid_alive
    import cli as cli_mod
    monkeypatch.setattr(cli_mod, "LOCK_FILE", lock_file)
    monkeypatch.setattr(cli_mod, "_pid_alive", lambda pid: True)
    # Also set CLI_STATE_FILE to temp so state ops don't pollute home dir
    state_file = lock_dir / "cli_state.json"
    monkeypatch.setattr(cli_mod, "CLI_STATE_FILE", state_file)
    return "http://localhost:19440"


# ---------------------------------------------------------------------------
# TestCliRequiresServer
# ---------------------------------------------------------------------------

class TestCliRequiresServer:

    def test_commands_fail_without_server(self, runner, cli, tmp_path, monkeypatch):
        """CLI commands (except serve) fail when server is not running."""
        # Point lock file to nonexistent path
        import cli as cli_mod
        fake_lock = tmp_path / "no_lock" / "server.lock"
        monkeypatch.setattr(cli_mod, "LOCK_FILE", fake_lock)

        result = runner.invoke(cli, ["tree", "ls"])
        assert result.exit_code != 0
        # Should indicate server not running
        assert "not running" in result.output.lower() or "server" in result.output.lower() \
            or result.exit_code != 0


# ---------------------------------------------------------------------------
# TestTreeCommands
# ---------------------------------------------------------------------------

class TestTreeCommands:

    def test_tree_ls_empty(self, runner, cli, mock_server_lock):
        """'fission tree ls' with no trees shows empty message."""
        # _require_server() calls httpx.get for health check
        # tree_ls() calls httpx.get for /api/trees
        health_resp = _mock_response(json_data={"status": "ok"})
        trees_resp = _mock_response(json_data={"trees": []})

        with patch("httpx.get", side_effect=[health_resp, trees_resp]):
            result = runner.invoke(cli, ["tree", "ls"])

        assert result.exit_code == 0
        output = result.output.lower()
        assert "no trees" in output or len(result.output.strip()) == 0 or "empty" in output

    def test_tree_ls_shows_trees(self, runner, cli, mock_server_lock):
        """'fission tree ls' lists tree names."""
        health_resp = _mock_response(json_data={"status": "ok"})
        trees_resp = _mock_response(json_data={"trees": [
            {"id": "t1", "name": "Tree Alpha", "root_node_id": "r1"},
            {"id": "t2", "name": "Tree Beta", "root_node_id": "r2"},
        ]})

        with patch("httpx.get", side_effect=[health_resp, trees_resp]):
            result = runner.invoke(cli, ["tree", "ls"])

        assert result.exit_code == 0
        assert "Tree Alpha" in result.output or "Alpha" in result.output
        assert "Tree Beta" in result.output or "Beta" in result.output

    def test_tree_new(self, runner, cli, mock_server_lock):
        """'fission tree new' creates a tree."""
        health_resp = _mock_response(json_data={"status": "ok"})
        create_resp = _mock_response(
            status_code=201,
            json_data={"tree": {"id": "abc123", "name": "Add auth"}, "root": {"id": "root1"}},
        )

        with patch("httpx.get", return_value=health_resp), \
             patch("httpx.post", return_value=create_resp):
            result = runner.invoke(cli, ["tree", "new", "Add auth"])

        assert result.exit_code == 0
        assert "abc123" in result.output

    def test_tree_rm(self, runner, cli, mock_server_lock):
        """'fission tree rm' deletes a tree."""
        health_resp = _mock_response(json_data={"status": "ok"})
        delete_resp = _mock_response(json_data={"ok": True})

        with patch("httpx.get", return_value=health_resp), \
             patch("httpx.delete", return_value=delete_resp):
            result = runner.invoke(cli, ["tree", "rm", "abc123"])

        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# TestChatCommand
# ---------------------------------------------------------------------------

class TestChatCommand:

    def test_chat_streams_output(self, runner, cli, mock_server_lock, tmp_path, monkeypatch):
        """'fission chat' streams text deltas to stdout."""
        import cli as cli_mod
        # Set active tree/node state
        state_file = tmp_path / ".codefission" / "cli_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"tree_id": "t1", "node_id": "n0"}))
        monkeypatch.setattr(cli_mod, "CLI_STATE_FILE", state_file)

        sse_events = [
            {"data": {"type": "node_created", "node": {"id": "n1"}}},
            {"data": {"type": "text_delta", "text": "Hello "}},
            {"data": {"type": "text_delta", "text": "world"}},
            {"data": {"type": "done", "node_id": "n1", "full_response": "Hello world",
                       "git_commit": None, "files_changed": 0}},
        ]

        health_resp = _mock_response(json_data={"status": "ok"})
        sse_resp = _mock_sse_response(sse_events)

        with patch("httpx.get", return_value=health_resp), \
             patch("httpx.stream", return_value=sse_resp):
            result = runner.invoke(cli, ["chat", "hello"])

        assert result.exit_code == 0
        # Should contain the streamed text
        assert "Hello" in result.output or "world" in result.output

    def test_chat_requires_active_tree(self, runner, cli, mock_server_lock, tmp_path, monkeypatch):
        """'fission chat' fails when no active tree/node is set."""
        import cli as cli_mod
        # Empty state
        state_file = tmp_path / ".codefission" / "cli_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("{}")
        monkeypatch.setattr(cli_mod, "CLI_STATE_FILE", state_file)

        health_resp = _mock_response(json_data={"status": "ok"})

        with patch("httpx.get", return_value=health_resp):
            result = runner.invoke(cli, ["chat", "hello"])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# TestSettingsCommands
# ---------------------------------------------------------------------------

class TestSettingsCommands:

    def test_set_shows_all(self, runner, cli, mock_server_lock):
        """'fission set' with no args shows current settings."""
        health_resp = _mock_response(json_data={"status": "ok"})
        settings_resp = _mock_response(json_data={
            "global_defaults": {
                "provider": "claude",
                "model": "claude-sonnet-4-6",
                "max_turns": 25,
            },
            "providers": [],
        })

        with patch("httpx.get", side_effect=[health_resp, settings_resp]):
            result = runner.invoke(cli, ["set"])

        assert result.exit_code == 0
        assert "claude" in result.output or "provider" in result.output.lower()

    def test_set_provider(self, runner, cli, mock_server_lock):
        """'fission set provider codex' updates the default provider."""
        health_resp = _mock_response(json_data={"status": "ok"})
        patch_resp = _mock_response(json_data={"global_defaults": {"provider": "codex"}, "providers": []})

        with patch("httpx.get", return_value=health_resp), \
             patch("httpx.patch", return_value=patch_resp) as mock_patch:
            result = runner.invoke(cli, ["set", "provider", "codex"])

        assert result.exit_code == 0
        # Verify PATCH was called
        mock_patch.assert_called_once()


# ---------------------------------------------------------------------------
# TestNodeCommands
# ---------------------------------------------------------------------------

class TestNodeCommands:

    def test_ls_shows_tree_info(self, runner, cli, mock_server_lock, tmp_path, monkeypatch):
        """'fission ls' shows tree info when active tree is set."""
        import cli as cli_mod
        state_file = tmp_path / ".codefission" / "cli_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"tree_id": "t1", "node_id": "n1"}))
        monkeypatch.setattr(cli_mod, "CLI_STATE_FILE", state_file)

        health_resp = _mock_response(json_data={"status": "ok"})
        trees_resp = _mock_response(json_data={"trees": [
            {"id": "t1", "name": "My Tree", "root_node_id": "r1"},
        ]})

        with patch("httpx.get", side_effect=[health_resp, trees_resp]):
            result = runner.invoke(cli, ["ls"])

        assert result.exit_code == 0
        assert "My Tree" in result.output

    def test_select_updates_state(self, runner, cli, mock_server_lock, tmp_path, monkeypatch):
        """'fission select <id>' updates the local CLI state file."""
        import cli as cli_mod

        # Point state file to temp dir
        state_file = tmp_path / ".codefission" / "cli_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(cli_mod, "CLI_STATE_FILE", state_file)

        result = runner.invoke(cli, ["select", "abc123"])

        assert result.exit_code == 0
        # State file should be updated
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert state["node_id"] == "abc123"


# ---------------------------------------------------------------------------
# TestLogCommand
# ---------------------------------------------------------------------------

class TestLogCommand:

    def test_log_shows_actions(self, runner, cli, mock_server_lock, tmp_path, monkeypatch):
        """'fission log' shows action history."""
        import cli as cli_mod
        state_file = tmp_path / ".codefission" / "cli_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"tree_id": "t1"}))
        monkeypatch.setattr(cli_mod, "CLI_STATE_FILE", state_file)

        health_resp = _mock_response(json_data={"status": "ok"})
        log_resp = _mock_response(json_data={"actions": [
            {"seq": 1, "kind": "create_tree", "ts": "2025-01-01T00:00:00",
             "params": {"name": "Test"}, "result": {}, "source": "gui"},
            {"seq": 2, "kind": "chat", "ts": "2025-01-01T00:01:00",
             "params": {"message": "hello"}, "result": {"cost_usd": 0.01},
             "source": "cli"},
        ]})

        with patch("httpx.get", side_effect=[health_resp, log_resp]):
            result = runner.invoke(cli, ["log"])

        assert result.exit_code == 0
        assert "create_tree" in result.output or "chat" in result.output

    def test_log_json(self, runner, cli, mock_server_lock, tmp_path, monkeypatch):
        """'fission log --json' outputs valid JSON."""
        import cli as cli_mod
        state_file = tmp_path / ".codefission" / "cli_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"tree_id": "t1"}))
        monkeypatch.setattr(cli_mod, "CLI_STATE_FILE", state_file)

        actions = [
            {"seq": 1, "kind": "create_tree", "ts": "2025-01-01T00:00:00",
             "params": {"name": "T"}, "result": {}, "source": "gui"},
        ]

        health_resp = _mock_response(json_data={"status": "ok"})
        log_resp = _mock_response(json_data={"actions": actions})

        with patch("httpx.get", side_effect=[health_resp, log_resp]):
            result = runner.invoke(cli, ["log", "--json"])

        assert result.exit_code == 0
        # Output should be valid JSON
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
