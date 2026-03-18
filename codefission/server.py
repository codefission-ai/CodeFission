"""Server launcher — finds a port, acquires lock, starts uvicorn."""

import argparse
import atexit
import json
import os
import shutil
import socket
import sys
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path.home() / ".codefission"
DEFAULT_PORT = 19440
PORT_RANGE = range(19440, 19450)
LOCK_FILE = DATA_DIR / "server.lock"
UPDATE_CHECK_FILE = DATA_DIR / "update_check.json"
UPDATE_CHECK_INTERVAL = timedelta(hours=24)


def _get_installed_version() -> str:
    try:
        from importlib.metadata import version
        return version("codefission")
    except Exception:
        return "0.0.0"


def _version_tuple(v: str):
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


def _load_update_cache() -> dict:
    try:
        if UPDATE_CHECK_FILE.exists():
            return json.loads(UPDATE_CHECK_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_update_cache(data: dict):
    UPDATE_CHECK_FILE.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_CHECK_FILE.write_text(json.dumps(data) + "\n")


def _fetch_latest_version() -> str | None:
    try:
        with urllib.request.urlopen(
            "https://pypi.org/pypi/codefission/json", timeout=3
        ) as resp:
            return json.loads(resp.read())["info"]["version"]
    except Exception:
        return None


def _check_for_update(force: bool = False) -> str | None:
    """Returns latest PyPI version if newer than installed (and not skipped), else None."""
    installed = _get_installed_version()
    now = datetime.now(timezone.utc)
    cache = _load_update_cache()

    if not force:
        last_check = cache.get("checked_at")
        if last_check:
            try:
                if now - datetime.fromisoformat(last_check) < UPDATE_CHECK_INTERVAL:
                    latest = cache.get("latest", installed)
                    skipped = cache.get("skipped")
                    if _version_tuple(latest) > _version_tuple(installed):
                        if skipped and _version_tuple(skipped) >= _version_tuple(latest):
                            return None  # user already said no to this version
                        return latest
                    return None
            except Exception:
                pass

    latest = _fetch_latest_version()
    if latest is None:
        return None

    cache["checked_at"] = now.isoformat()
    cache["latest"] = latest
    _save_update_cache(cache)

    if _version_tuple(latest) > _version_tuple(installed):
        skipped = cache.get("skipped")
        if not force and skipped and _version_tuple(skipped) >= _version_tuple(latest):
            return None  # user already said no to this version
        return latest
    return None


def _do_upgrade():
    import subprocess
    print()

    # uv tool installations have no pip — use `uv tool upgrade` instead
    if "uv/tools" in sys.executable or "uv\\tools" in sys.executable:
        uv = shutil.which("uv")
        if uv:
            result = subprocess.run([uv, "tool", "upgrade", "codefission"])
            if result.returncode == 0:
                print("\n  Upgraded! Restarting fission...\n")
                os.execv(sys.argv[0], sys.argv)
            else:
                print("\n  Upgrade failed. Try: uv tool upgrade codefission", file=sys.stderr)
                sys.exit(1)
            return
        print("\n  Upgrade failed. Try: uv tool upgrade codefission", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", "codefission"]
    )
    if result.returncode == 0:
        print("\n  Upgraded! Restarting fission...\n")
        os.execv(sys.argv[0], sys.argv)
    else:
        print("\n  Upgrade failed. Try: pip install -U codefission", file=sys.stderr)
        sys.exit(1)


def _prompt_update(latest: str, force: bool = False):
    installed = _get_installed_version()
    print(f"\n  A new version of CodeFission is available: {latest}  (you have {installed})\n")

    if not sys.stdin.isatty():
        print(f"  Run `pip install -U codefission` to upgrade.")
        return

    try:
        answer = input("  Upgrade now? [Y/n/skip] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if answer in ("", "y", "yes"):
        _do_upgrade()
    elif answer in ("s", "skip"):
        cache = _load_update_cache()
        cache["skipped"] = latest
        _save_update_cache(cache)
        print(f"\n  Skipping {latest}. You won't be reminded until a newer version is out.\n")
    else:
        # "no" — remind again next time the 24h cache expires
        print(f"\n  Reminder: run `pip install -U codefission` to upgrade.\n")


def _find_chromium_binary() -> str | None:
    """Return the path to a Chromium-based browser binary, or None."""
    import platform

    system = platform.system()
    if system == "Darwin":
        for app in [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
            "/Applications/Opera.app/Contents/MacOS/Opera",
        ]:
            if os.path.isfile(app):
                return app
    elif system == "Linux":
        for b in [
            "google-chrome", "google-chrome-stable",
            "chromium", "chromium-browser",
            "brave-browser", "microsoft-edge",
            "vivaldi", "opera",
        ]:
            path = shutil.which(b)
            if path:
                return path
    elif system == "Windows":
        for b in ["chrome", "msedge", "brave", "chromium"]:
            path = shutil.which(b)
            if path:
                return path
    return None


_CODEFISSION_APP = Path.home() / ".codefission" / "CodeFission.app"


def _ensure_macos_app(chrome_binary: str, port: int) -> Path:
    """Create or update a lightweight .app bundle that wraps Chrome in --app mode."""
    app = _CODEFISSION_APP
    launcher = app / "Contents" / "MacOS" / "launch"

    expected_script = (
        f'#!/usr/bin/env bash\n'
        f'exec "{chrome_binary}" --app="http://localhost:{port}" '
        f'--user-data-dir="$HOME/.codefission/chrome-profile"\n'
    )

    # Rebuild only if missing or launcher changed (different browser/port)
    if launcher.is_file() and launcher.read_text() == expected_script:
        return app

    contents = app / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    plist = contents / "Info.plist"
    plist.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n'
        '  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        '    <key>CFBundleName</key><string>CodeFission</string>\n'
        '    <key>CFBundleDisplayName</key><string>CodeFission</string>\n'
        '    <key>CFBundleIdentifier</key><string>com.codefission.app</string>\n'
        '    <key>CFBundleVersion</key><string>1.0</string>\n'
        '    <key>CFBundlePackageType</key><string>APPL</string>\n'
        '    <key>CFBundleExecutable</key><string>launch</string>\n'
        '</dict>\n</plist>\n'
    )

    launcher.write_text(expected_script)
    launcher.chmod(0o755)

    return app


def _open_browser(url: str):
    """Open url in a Chromium-based browser app window, falling back to system default."""
    import platform
    import subprocess
    from urllib.parse import urlparse

    system = platform.system()
    chrome = _find_chromium_binary()

    if chrome:
        # Extract port from URL for the .app bundle
        parsed = urlparse(url)
        port = parsed.port or 19440

        if system == "Darwin":
            app_path = _ensure_macos_app(chrome, port)
            try:
                subprocess.Popen(["open", "-a", str(app_path)])
                return
            except Exception:
                pass
            # Fall back to direct binary launch
            try:
                subprocess.Popen([chrome, f"--app={url}",
                                  "--user-data-dir=" + str(Path.home() / ".codefission" / "chrome-profile")])
                return
            except Exception:
                pass
        else:
            try:
                subprocess.Popen([chrome, f"--app={url}"])
                return
            except Exception:
                pass

    # Fall back to system default
    webbrowser.open(url)


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
        _open_browser(f"http://localhost:{existing_port}")
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
        "--version", action="version", version=f"codefission {_get_installed_version()}",
    )
    parser.add_argument(
        "--update", action="store_true",
        help="Check for updates and prompt to upgrade",
    )
    args = parser.parse_args()

    if args.update:
        latest = _check_for_update(force=True)
        if latest:
            _prompt_update(latest, force=True)
        else:
            print(f"  codefission {_get_installed_version()} is up to date.")
        sys.exit(0)

    latest = _check_for_update()
    if latest:
        _prompt_update(latest)

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
    # connections. We use loop.add_signal_handler with os._exit().
    class FastExitServer(uvicorn.Server):
        def install_signal_handlers(self):
            import asyncio as _asyncio
            loop = _asyncio.get_event_loop()
            print(f"[server] Installing fast-exit signal handlers on loop {id(loop)}")
            for sig in (2, 15):  # SIGINT, SIGTERM
                loop.add_signal_handler(sig, self._fast_exit)

        def _fast_exit(self):
            print("\nCtrl+C received. Exiting.", flush=True)
            _release_lock()
            os._exit(0)

    server = FastExitServer(config)
    server.run()


if __name__ == "__main__":
    main()
