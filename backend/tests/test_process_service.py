"""Tests for process_service — spawns real subprocesses to verify /proc scanning."""

import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

from services import process_service
from services.process_service import list_processes, kill_process, kill_all_in_workspace, kill_process_tree


def _free_port():
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _spawn(args, cwd, **kwargs):
    return subprocess.Popen(
        args, cwd=str(cwd),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
        **kwargs,
    )


def _cleanup(proc):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    proc.wait()


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture(autouse=True)
def fake_server_pid():
    """Set server_pid to a fake value so test-spawned children aren't filtered."""
    original = process_service._server_pid
    process_service._server_pid = 999_999_999
    yield
    process_service._server_pid = original


# ── Basic detection ─────────────────────────────────────────────────


def test_finds_process_in_workspace(workspace):
    proc = _spawn(["sleep", "60"], workspace)
    try:
        time.sleep(0.3)
        found = list_processes(workspace)
        pids = [p.pid for p in found]
        assert proc.pid in pids
    finally:
        _cleanup(proc)


def test_ignores_process_outside_workspace(workspace, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    proc = _spawn(["sleep", "60"], other)
    try:
        time.sleep(0.3)
        found = list_processes(workspace)
        pids = [p.pid for p in found]
        assert proc.pid not in pids
    finally:
        _cleanup(proc)


def test_finds_process_in_subdirectory(workspace):
    sub = workspace / "src"
    sub.mkdir()
    proc = _spawn(["sleep", "60"], sub)
    try:
        time.sleep(0.3)
        found = list_processes(workspace)
        pids = [p.pid for p in found]
        assert proc.pid in pids
    finally:
        _cleanup(proc)


def test_returns_command_line(workspace):
    proc = _spawn(["python3", "-c", "import time; time.sleep(60)"], workspace)
    try:
        time.sleep(0.3)
        found = list_processes(workspace)
        match = [p for p in found if p.pid == proc.pid]
        assert len(match) == 1
        assert "python3" in match[0].command
    finally:
        _cleanup(proc)


# ── Port detection ──────────────────────────────────────────────────


def test_detects_listening_port(workspace):
    port = _free_port()
    proc = _spawn(["python3", "-m", "http.server", str(port)], workspace)
    try:
        time.sleep(1.5)
        found = list_processes(workspace)
        match = [p for p in found if "http.server" in p.command]
        assert len(match) >= 1, f"http.server not found, got: {[p.command[:60] for p in found]}"
        assert port in match[0].ports, f"port {port} not in {match[0].ports}"
    finally:
        _cleanup(proc)


# ── ppid filter ─────────────────────────────────────────────────────


def test_filters_direct_child_of_server(workspace):
    """Process with ppid == server_pid should be filtered (SDK, git helpers)."""
    process_service._server_pid = os.getpid()
    proc = _spawn(["sleep", "60"], workspace)
    try:
        time.sleep(0.3)
        found = list_processes(workspace)
        pids = [p.pid for p in found]
        # start_new_session=True changes the session but NOT the parent.
        # ppid is still our PID, so this should be filtered.
        assert proc.pid not in pids, "Direct child of server should be filtered"
    finally:
        _cleanup(proc)


# ── Claude shell wrapper filter ─────────────────────────────────────


def test_filters_claude_shell_wrapper(workspace, tmp_path):
    """zsh wrapper with .claude/shell-snapshots/ in cmdline should be filtered."""
    snapshot_dir = tmp_path / ".claude" / "shell-snapshots"
    snapshot_dir.mkdir(parents=True)
    script = snapshot_dir / "snapshot.sh"
    script.write_text("# snapshot\n")

    wrapper = _spawn(
        ["/usr/bin/zsh", "-c", f"source {script} && sleep 60"],
        workspace,
    )
    try:
        time.sleep(0.5)
        found = list_processes(workspace)

        # zsh may exec sleep (last-command optimization), in which case
        # wrapper.pid's cmdline becomes "sleep 60" with no snapshot path.
        # Either way, the snapshot wrapper itself should not appear.
        snapshot_procs = [p for p in found if ".claude/shell-snapshots/" in p.command]
        assert len(snapshot_procs) == 0, f"Shell wrapper should be filtered: {snapshot_procs}"
    finally:
        _cleanup(wrapper)


def test_finds_server_inside_claude_wrapper(workspace, tmp_path):
    """python http.server started via Claude's zsh wrapper should be found."""
    snapshot_dir = tmp_path / ".claude" / "shell-snapshots"
    snapshot_dir.mkdir(parents=True)
    script = snapshot_dir / "snapshot.sh"
    script.write_text("# snapshot\n")

    port = _free_port()
    # Mimic Claude Code: zsh wrapper with eval (prevents exec optimization)
    cmd = f"source {script} && eval 'python3 -m http.server {port}'"
    wrapper = _spawn(["/usr/bin/zsh", "-c", cmd], workspace)
    try:
        time.sleep(1.5)
        found = list_processes(workspace)

        # The zsh wrapper should be filtered
        snapshot_procs = [p for p in found if ".claude/shell-snapshots/" in p.command]
        assert len(snapshot_procs) == 0, f"Wrapper should be filtered: {snapshot_procs}"

        # The python http.server should be found with the correct port
        servers = [p for p in found if "http.server" in p.command]
        assert len(servers) >= 1, (
            f"http.server not found. All processes: "
            f"{[(p.pid, p.command[:80]) for p in found]}"
        )
        assert port in servers[0].ports
    finally:
        _cleanup(wrapper)


# ── Kill ────────────────────────────────────────────────────────────


def test_kill_process_in_workspace(workspace):
    proc = _spawn(["sleep", "60"], workspace)
    time.sleep(0.3)
    assert kill_process(proc.pid, workspace) is True
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        _cleanup(proc)
        pytest.fail("Process did not die after kill_process")


def test_kill_also_kills_descendants(workspace):
    """Killing a parent should also kill its child processes."""
    # Parent spawns a child that sleeps
    parent = _spawn(
        ["python3", "-c",
         "import subprocess, time; "
         "subprocess.Popen(['sleep', '60']); "
         "time.sleep(60)"],
        workspace,
    )
    time.sleep(0.5)

    # Find the child sleep process
    found_before = list_processes(workspace)
    sleep_procs = [p for p in found_before if p.command.strip() == "sleep 60"]
    assert len(sleep_procs) >= 1, f"Child sleep not found: {[p.command for p in found_before]}"
    child_pid = sleep_procs[0].pid

    # Kill the parent — should also kill the child
    assert kill_process(parent.pid, workspace) is True
    time.sleep(0.5)

    # Verify both are gone
    try:
        os.kill(child_pid, 0)  # check if alive
        # Still alive — clean up and fail
        _cleanup(parent)
        pytest.fail(f"Child PID {child_pid} survived after parent was killed")
    except ProcessLookupError:
        pass  # child is dead, good

    try:
        parent.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _cleanup(parent)
        pytest.fail("Parent did not die")


def test_kill_refuses_outside_workspace(workspace, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    proc = _spawn(["sleep", "60"], other)
    try:
        time.sleep(0.3)
        assert kill_process(proc.pid, workspace) is False
        assert proc.poll() is None, "Process should still be alive"
    finally:
        _cleanup(proc)


def test_kill_refuses_server_pid(workspace):
    process_service._server_pid = os.getpid()
    assert kill_process(os.getpid(), workspace) is False


def test_kill_all_in_workspace(workspace):
    procs = []
    for _ in range(3):
        p = _spawn(["sleep", "60"], workspace)
        procs.append(p)
    time.sleep(0.3)
    killed = kill_all_in_workspace(workspace)
    assert killed == 3
    try:
        for p in procs:
            p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        for p in procs:
            _cleanup(p)
        pytest.fail("Some processes did not die after kill_all")


# ── Edge cases ──────────────────────────────────────────────────────


def test_kill_process_tree(workspace):
    """kill_process_tree should kill a process and all its descendants."""
    # Parent spawns a child that sleeps
    parent = _spawn(
        ["python3", "-c",
         "import subprocess, time; "
         "subprocess.Popen(['sleep', '60']); "
         "time.sleep(60)"],
        workspace,
    )
    time.sleep(0.5)

    # Find the child sleep process
    found = list_processes(workspace)
    sleep_procs = [p for p in found if p.command.strip() == "sleep 60"]
    assert len(sleep_procs) >= 1, f"Child not found: {[p.command for p in found]}"
    child_pid = sleep_procs[0].pid

    # Kill the tree
    kill_process_tree(parent.pid)

    # Reap the parent (it becomes a zombie after SIGKILL until wait())
    try:
        parent.wait(timeout=3)
    except subprocess.TimeoutExpired:
        _cleanup(parent)
        pytest.fail("Parent did not die")

    time.sleep(0.3)

    # Child should also be dead
    try:
        os.kill(child_pid, 0)
        pytest.fail(f"Child PID {child_pid} should be dead after kill_process_tree")
    except ProcessLookupError:
        pass


def test_kill_process_tree_does_not_kill_server():
    """kill_process_tree should refuse to kill the server process."""
    process_service._server_pid = os.getpid()
    kill_process_tree(os.getpid())
    # If we got here, we're still alive
    assert True


def test_exited_process_not_found(workspace):
    proc = _spawn(["true"], workspace)
    proc.wait()
    time.sleep(0.3)
    found = list_processes(workspace)
    pids = [p.pid for p in found]
    assert proc.pid not in pids
