"""Linux process backend — reads /proc filesystem."""

import os
from pathlib import Path

_PROC = Path("/proc")


def available() -> bool:
    return _PROC.exists()


def snapshot_processes() -> list[tuple[int, int, str, str]]:
    """Return (pid, ppid, cwd, cmdline) for all accessible processes."""
    result = []
    try:
        entries = list(_PROC.iterdir())
    except PermissionError:
        return []

    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat_text = (entry / "stat").read_text()
            ppid = int(stat_text.split(")")[1].split()[1])
            cwd = str((entry / "cwd").resolve())
            cmdline = (entry / "cmdline").read_bytes().decode(errors="replace")
            cmdline = cmdline.replace("\x00", " ").strip()
            if not cmdline:
                continue
            result.append((int(entry.name), ppid, cwd, cmdline))
        except (PermissionError, FileNotFoundError, ProcessLookupError, OSError,
                IndexError, ValueError):
            continue

    return result


def get_process_cwd(pid: int) -> str | None:
    """Get cwd of a specific process."""
    try:
        return str((_PROC / str(pid) / "cwd").resolve())
    except (PermissionError, FileNotFoundError, ProcessLookupError, OSError):
        return None


def get_listening_ports(pid: int) -> list[int]:
    """Get TCP ports this process is listening on via /proc/net/tcp."""
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
