"""Process tracking — find and manage processes spawned by agent nodes.

Discovers processes whose cwd is under a node's workspace directory. This
catches orphaned servers, dev tools, and background scripts that the agent
started but that outlive the conversation turn.

Platform backends:
  - Linux:  /proc filesystem          → _process_linux.py
  - macOS:  ps + lsof                 → _process_darwin.py
  - Other:  returns empty (graceful)
"""

import os
import signal
import sys
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


# ── Platform backend dispatch ──────────────────────────────────────────

def _load_backend():
    if sys.platform == "linux":
        from services import _process_linux as backend
        return backend
    elif sys.platform == "darwin":
        from services import _process_darwin as backend
        return backend
    return None


_backend = _load_backend()


# Our PID — direct children are infrastructure (SDK, git helpers).
# User-started processes are grandchildren or deeper, and get reparented to
# PID 1 once the agent exits, so they're never direct children of ours.
_server_pid: int = 0


def _get_server_pid() -> int:
    global _server_pid
    if not _server_pid:
        _server_pid = os.getpid()
    return _server_pid


@dataclass
class ProcessInfo:
    pid: int
    command: str
    ports: list[int] = field(default_factory=list)


def list_processes(workspace: Path) -> list[ProcessInfo]:
    """Find processes with cwd under the given workspace path."""
    if _backend is None or not _backend.available():
        return []

    workspace_str = str(workspace.resolve())
    server_pid = _get_server_pid()
    result: list[ProcessInfo] = []

    for pid, ppid, cwd, cmdline in _backend.snapshot_processes():
        if pid == server_pid:
            continue
        # Skip direct children of our server (SDK subprocess, git helpers).
        # User-started processes are grandchildren+ or orphans (ppid=1).
        if ppid == server_pid:
            continue
        if not cwd.startswith(workspace_str):
            continue
        # Skip Claude Code's shell wrappers for tool execution
        if ".claude/shell-snapshots/" in cmdline:
            continue
        ports = _backend.get_listening_ports(pid)
        result.append(ProcessInfo(pid=pid, command=cmdline, ports=ports))

    return result


def list_tree_processes(tree_workspace: Path) -> dict[str, list[ProcessInfo]]:
    """Single scan for an entire tree, grouped by node_id.

    tree_workspace is WORKSPACES_DIR / tree_id. Each node's workspace is
    a direct subdirectory: tree_workspace / node_id. We extract the node_id
    from each process's cwd to group them.
    """
    if _backend is None or not _backend.available():
        return {}

    tree_ws_str = str(tree_workspace.resolve())
    server_pid = _get_server_pid()
    grouped: dict[str, list[ProcessInfo]] = {}

    for pid, ppid, cwd, cmdline in _backend.snapshot_processes():
        if pid == server_pid:
            continue
        if ppid == server_pid:
            continue
        if not cwd.startswith(tree_ws_str + "/") and cwd != tree_ws_str:
            continue
        if ".claude/shell-snapshots/" in cmdline:
            continue

        # Extract node_id: first path component after tree_workspace
        relative = cwd[len(tree_ws_str):].lstrip("/")
        node_id = relative.split("/")[0] if relative else ""
        if not node_id:
            continue

        ports = _backend.get_listening_ports(pid)
        grouped.setdefault(node_id, []).append(
            ProcessInfo(pid=pid, command=cmdline, ports=ports)
        )

    return grouped


def find_child_by_cwd(workspace: Path) -> int | None:
    """Find a direct child of the server process whose cwd is under workspace.

    Used to locate the SDK subprocess spawned for a chat.
    """
    if _backend is None or not _backend.available():
        return None

    server_pid = _get_server_pid()
    workspace_str = str(workspace.resolve())

    for pid, ppid, cwd, _cmdline in _backend.snapshot_processes():
        if ppid != server_pid:
            continue
        if cwd.startswith(workspace_str):
            return pid
    return None


def _get_descendants(pid: int) -> list[int]:
    """Find all descendant PIDs (children, grandchildren, etc.)."""
    if _backend is None or not _backend.available():
        return []

    children_map: dict[int, list[int]] = {}
    for p_pid, p_ppid, _, _ in _backend.snapshot_processes():
        children_map.setdefault(p_ppid, []).append(p_pid)

    # BFS from pid
    result = []
    queue = children_map.get(pid, [])
    while queue:
        child = queue.pop()
        result.append(child)
        queue.extend(children_map.get(child, []))
    return result


def kill_process(pid: int, workspace: Path) -> bool:
    """Kill a process and all its descendants, after verifying cwd is under workspace."""
    if pid == _get_server_pid():
        return False

    workspace_str = str(workspace.resolve())
    try:
        if _backend is not None and _backend.available():
            cwd = _backend.get_process_cwd(pid)
        else:
            cwd = None

        if cwd is not None and not cwd.startswith(workspace_str):
            log.warning("Refusing to kill PID %d: cwd %s not under %s", pid, cwd, workspace_str)
            return False

        # Kill descendants first (bottom-up), then the target
        descendants = _get_descendants(pid)
        for child_pid in reversed(descendants):
            try:
                os.kill(child_pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, FileNotFoundError, OSError) as e:
        log.warning("Failed to kill PID %d: %s", pid, e)
        return False


def kill_process_tree(pid: int) -> None:
    """Kill a process and all its descendants by PID (no workspace check).

    Used for killing the SDK subprocess and everything it spawned (curl, bash, etc.)
    when a chat is cancelled.
    """
    server_pid = _get_server_pid()
    if pid == server_pid:
        return
    try:
        descendants = _get_descendants(pid)
        # Kill children first (bottom-up), then the target
        for child_pid in reversed(descendants):
            try:
                os.kill(child_pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError) as e:
        log.debug("kill_process_tree(%d): %s", pid, e)


def kill_all_in_workspace(workspace: Path) -> int:
    """Kill all processes under workspace. Returns count killed."""
    procs = list_processes(workspace)
    killed = 0
    for p in procs:
        if kill_process(p.pid, workspace):
            killed += 1
    return killed
