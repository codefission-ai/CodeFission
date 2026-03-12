"""macOS process backend — uses ps and lsof."""

import subprocess


def available() -> bool:
    return True


def snapshot_processes() -> list[tuple[int, int, str, str]]:
    """Return (pid, ppid, cwd, cmdline) for all accessible processes.

    Uses two shell commands:
      1. ``ps -axo pid=,ppid=,command=`` for pid/ppid/cmdline
      2. ``lsof -d cwd -Fpn`` for each process's working directory
    """
    # Step 1: pid, ppid, command for all processes
    try:
        ps_out = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,command="],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if ps_out.returncode != 0:
        return []

    ps_data: dict[int, tuple[int, str]] = {}
    for line in ps_out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            cmdline = parts[2]
            ps_data[pid] = (ppid, cmdline)
        except (ValueError, IndexError):
            continue

    # Step 2: cwd for all processes via lsof
    try:
        lsof_out = subprocess.run(
            ["lsof", "-d", "cwd", "-Fpn"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []

    cwd_map: dict[int, str] = {}
    current_pid = None
    for line in lsof_out.stdout.splitlines():
        if line.startswith("p"):
            try:
                current_pid = int(line[1:])
            except ValueError:
                current_pid = None
        elif line.startswith("n") and current_pid is not None:
            cwd_map[current_pid] = line[1:]

    # Combine: only include processes where we got both ps data and cwd
    result = []
    for pid, (ppid, cmdline) in ps_data.items():
        cwd = cwd_map.get(pid, "")
        if not cwd:
            continue
        result.append((pid, ppid, cwd, cmdline))

    return result


def get_process_cwd(pid: int) -> str | None:
    """Get cwd of a specific process."""
    try:
        out = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    for line in out.stdout.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def get_listening_ports(pid: int) -> list[int]:
    """Get TCP ports this process is listening on."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid), "-Fn"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []

    ports: set[int] = set()
    for line in out.stdout.splitlines():
        if line.startswith("n"):
            # Format: n*:PORT or nHOST:PORT
            name = line[1:]
            if ":" in name:
                try:
                    port = int(name.rsplit(":", 1)[1])
                    ports.add(port)
                except (ValueError, IndexError):
                    continue
    return sorted(ports)
