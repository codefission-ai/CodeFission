"""Server launcher — finds a port, acquires lock, starts uvicorn."""

import argparse
import atexit
import json
import os
import shutil
import socket
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path.home() / ".codefission"
DEFAULT_PORT = 19440
PORT_RANGE = range(19440, 19450)
LOCK_FILE = DATA_DIR / "server.lock"


def _check_prerequisites():
    missing = []
    if not shutil.which("git"):
        missing.append(
            "git - install from https://git-scm.com/downloads"
            "\n      macOS: xcode-select --install"
            "\n      Ubuntu/Debian: sudo apt install git"
        )
    if missing:
        print("CodeFission requires the following:\n")
        for m in missing:
            print(f"  * {m}\n")
        sys.exit(1)


def _is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _find_available_port(preferred: int) -> int | None:
    if _is_port_available(preferred):
        return preferred
    for port in PORT_RANGE:
        if port != preferred and _is_port_available(port):
            return port
    return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _read_lock() -> dict | None:
    if not LOCK_FILE.exists():
        return None
    try:
        data = json.loads(LOCK_FILE.read_text())
        pid = data.get("pid")
        if pid and _pid_alive(pid):
            return data
    except Exception:
        pass
    return None


def _acquire_lock(port: int):
    existing = _read_lock()
    if existing:
        existing_port = existing.get("port", "?")
        print(f"CodeFission is already running at http://localhost:{existing_port}")
        webbrowser.open(f"http://localhost:{existing_port}")
        sys.exit(0)

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(json.dumps({
        "pid": os.getpid(),
        "port": port,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }) + "\n")
    atexit.register(_release_lock)


def _release_lock():
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def main():
    """Entry point for `fission`. Just starts the server — no repo binding."""
    import uvicorn

    parser = argparse.ArgumentParser(
        prog="fission",
        description="CodeFission — tree-structured AI development.",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"Server port (default: {DEFAULT_PORT})",
    )
    args = parser.parse_args()

    _check_prerequisites()

    actual_port = _find_available_port(args.port)
    if actual_port is None:
        print(f"Error: No available port in range {PORT_RANGE.start}-{PORT_RANGE.stop - 1}.", file=sys.stderr)
        raise SystemExit(1)

    _acquire_lock(actual_port)

    os.environ["CODEFISSION_PORT"] = str(actual_port)

    print(f"Server:  http://localhost:{actual_port}")

    config = uvicorn.Config(
        "codefission.main:app",
        host="0.0.0.0",
        port=actual_port,
        ws_ping_interval=30,
        ws_ping_timeout=10,
        loop="asyncio",
    )

    # Subclass Server to prevent uvicorn from installing its own signal
    # handlers. Uvicorn's graceful shutdown hangs on open WebSocket
    # connections. We handle SIGINT ourselves with os._exit().
    class FastExitServer(uvicorn.Server):
        def install_signal_handlers(self):
            import signal
            def _die(sig, frame):
                print("\nShutting down...")
                _release_lock()
                os._exit(0)
            signal.signal(signal.SIGINT, _die)
            signal.signal(signal.SIGTERM, _die)

    server = FastExitServer(config)
    server.run()


if __name__ == "__main__":
    main()
