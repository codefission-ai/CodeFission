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
        url = f"http://localhost:{existing_port}"
        print(f"CodeFission is already running at {url}")
        if os.environ.get("CODEFISSION_PYWEBVIEW"):
            _open_webview_window(url)
        else:
            webbrowser.open(url)
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
    parser.add_argument(
        "--pywebview", action="store_true",
        help="Open in a native desktop window instead of the browser",
    )
    args = parser.parse_args()

    _check_prerequisites()

    actual_port = _find_available_port(args.port)
    if actual_port is None:
        print(f"Error: No available port in range {PORT_RANGE.start}-{PORT_RANGE.stop - 1}.", file=sys.stderr)
        raise SystemExit(1)

    if args.pywebview:
        os.environ["CODEFISSION_PYWEBVIEW"] = "1"

    _acquire_lock(actual_port)

    os.environ["CODEFISSION_PORT"] = str(actual_port)

    print(f"Server:  http://localhost:{actual_port}")

    if args.pywebview:
        _run_with_pywebview(actual_port)
    else:
        uvicorn.run(
            "codefission.main:app",
            host="0.0.0.0",
            port=actual_port,
            ws_ping_interval=30,
            ws_ping_timeout=10,
        )


class _WebViewApi:
    """JS-callable API for pywebview window controls."""

    def __init__(self):
        self._window = None

    def set_window(self, window):
        self._window = window

    def close_window(self):
        if self._window:
            import threading
            threading.Timer(0.15, self._window.destroy).start()

    def minimize_window(self):
        if self._window:
            self._window.minimize()

    def toggle_fullscreen(self):
        if self._window:
            self._window.toggle_fullscreen()


def _open_webview_window(url: str):
    """Create and show a pywebview native window."""
    webview = _import_webview()
    api = _WebViewApi()
    window = webview.create_window(
        "CodeFission",
        url=url,
        js_api=api,
        width=1280,
        height=850,
        min_size=(600, 400),
        background_color="#000000",
        frameless=True,
        easy_drag=True,
    )
    api.set_window(window)
    webview.start()


def _webview_path() -> str:
    """Return path to the bundled pywebview source."""
    return str(Path(__file__).resolve().parent.parent / "ui" / "pywebview")


def _import_webview():
    """Import webview from the bundled pywebview source."""
    wv_path = _webview_path()
    if wv_path not in sys.path:
        sys.path.insert(0, wv_path)
    try:
        import webview
        return webview
    except ImportError:
        print(
            "Error: pywebview not found.\n"
            f"  Expected at: {wv_path}",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _run_with_pywebview(port: int):
    """Run uvicorn in a background thread and open a pywebview native window."""
    import threading
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(
        "codefission.main:app",
        host="0.0.0.0",
        port=port,
        ws_ping_interval=30,
        ws_ping_timeout=10,
    ))

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for uvicorn to be ready
    import time
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)

    _open_webview_window(f"http://localhost:{port}")

    # Window closed — shut down the server
    server.should_exit = True
    thread.join(timeout=3)


if __name__ == "__main__":
    main()
