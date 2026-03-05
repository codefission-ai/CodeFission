"""Process tracking — find and manage processes spawned by agent nodes.

Scans /proc to find processes whose cwd is under a node's workspace directory.
This catches orphaned servers, dev tools, and background scripts that the agent
started but that outlive the conversation turn.

Linux-only (/proc). Returns empty on other platforms.
"""

import os
import signal
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_PROC = Path("/proc")

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


def _get_listening_ports(pid: int) -> list[int]:
    """Get TCP ports this process is listening on via /proc/net/tcp."""
    # Collect socket inodes owned by this pid
    socket_inodes: set[str] = set()
    fd_dir = _PROC / str(pid) / "fd"
    try:
        for fd in fd_dir.iterdir():
            try:
                target = os.readlink(str(fd))
                if target.startswith("socket:["):
                    socket_inodes.add(target[8:-1])
            except (FileNotFoundError, PermissionError, OSError):
                continue
    except (FileNotFoundError, PermissionError):
        return []

    if not socket_inodes:
        return []

    ports: set[int] = set()
    for tcp_file in ("net/tcp", "net/tcp6"):
        tcp_path = _PROC / tcp_file
        try:
            for line in tcp_path.read_text().splitlines()[1:]:
                parts = line.split()
                if len(parts) < 10:
                    continue
                if parts[3] != "0A":  # 0A = LISTEN
                    continue
                if parts[9] in socket_inodes:
                    port_hex = parts[1].split(":")[1]
                    ports.add(int(port_hex, 16))
        except (FileNotFoundError, PermissionError):
            continue

    return sorted(ports)


def list_processes(workspace: Path) -> list[ProcessInfo]:
    """Find processes with cwd under the given workspace path."""
    if not _PROC.exists():
        return []

    workspace_str = str(workspace.resolve())
    server_pid = _get_server_pid()
    result: list[ProcessInfo] = []

    try:
        entries = list(_PROC.iterdir())
    except PermissionError:
        return []

    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == server_pid:
            continue

        try:
            # Skip direct children of our server (SDK subprocess, git helpers).
            # User-started processes are grandchildren+ or orphans (ppid=1).
            stat_text = (entry / "stat").read_text()
            ppid = int(stat_text.split(")")[1].split()[1])
            if ppid == server_pid:
                continue

            cwd = str((entry / "cwd").resolve())
            if not cwd.startswith(workspace_str):
                continue

            cmdline = (entry / "cmdline").read_bytes().decode(errors="replace")
            cmdline = cmdline.replace("\x00", " ").strip()
            if not cmdline:
                continue

            # Skip Claude Code's shell wrappers for tool execution
            if ".claude/shell-snapshots/" in cmdline:
                continue

            ports = _get_listening_ports(pid)
            result.append(ProcessInfo(pid=pid, command=cmdline, ports=ports))

        except (PermissionError, FileNotFoundError, ProcessLookupError, OSError,
                IndexError, ValueError):
            continue

    return result


def list_tree_processes(tree_workspace: Path) -> dict[str, list[ProcessInfo]]:
    """Single /proc scan for an entire tree, grouped by node_id.

    tree_workspace is WORKSPACES_DIR / tree_id. Each node's workspace is
    a direct subdirectory: tree_workspace / node_id. We extract the node_id
    from each process's cwd to group them.
    """
    if not _PROC.exists():
        return {}

    tree_ws_str = str(tree_workspace.resolve())
    server_pid = _get_server_pid()
    grouped: dict[str, list[ProcessInfo]] = {}

    try:
        entries = list(_PROC.iterdir())
    except PermissionError:
        return {}

    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == server_pid:
            continue

        try:
            stat_text = (entry / "stat").read_text()
            ppid = int(stat_text.split(")")[1].split()[1])
            if ppid == server_pid:
                continue

            cwd = str((entry / "cwd").resolve())
            if not cwd.startswith(tree_ws_str + "/") and cwd != tree_ws_str:
                continue

            cmdline = (entry / "cmdline").read_bytes().decode(errors="replace")
            cmdline = cmdline.replace("\x00", " ").strip()
            if not cmdline:
                continue

            if ".claude/shell-snapshots/" in cmdline:
                continue

            # Extract node_id: first path component after tree_workspace
            relative = cwd[len(tree_ws_str):].lstrip("/")
            node_id = relative.split("/")[0] if relative else ""
            if not node_id:
                continue

            ports = _get_listening_ports(pid)
            grouped.setdefault(node_id, []).append(
                ProcessInfo(pid=pid, command=cmdline, ports=ports)
            )

        except (PermissionError, FileNotFoundError, ProcessLookupError, OSError,
                IndexError, ValueError):
            continue

    return grouped


def _get_descendants(pid: int) -> list[int]:
    """Walk /proc to find all descendant PIDs (children, grandchildren, etc.)."""
    children_map: dict[int, list[int]] = {}
    for entry in _PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat_text = (entry / "stat").read_text()
            ppid = int(stat_text.split(")")[1].split()[1])
            children_map.setdefault(ppid, []).append(int(entry.name))
        except (PermissionError, FileNotFoundError, ProcessLookupError,
                OSError, IndexError, ValueError):
            continue

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
        cwd = str(Path(f"/proc/{pid}/cwd").resolve())
        if not cwd.startswith(workspace_str):
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


def kill_all_in_workspace(workspace: Path) -> int:
    """Kill all processes under workspace. Returns count killed."""
    procs = list_processes(workspace)
    killed = 0
    for p in procs:
        if kill_process(p.pid, workspace):
            killed += 1
    return killed
