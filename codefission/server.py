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

ELECTRON_VERSION = "33.3.1"
ELECTRON_DIR = DATA_DIR / "electron"


def _get_electron_binary() -> str | None:
    """Return the path to the Electron binary, downloading it if needed."""
    import platform
    import zipfile

    system = platform.system()
    machine = platform.machine()

    # Platform mapping for Electron release filenames
    if system == "Darwin":
        arch = "arm64" if machine == "arm64" else "x64"
        zipname = f"electron-v{ELECTRON_VERSION}-darwin-{arch}.zip"
        binary = ELECTRON_DIR / "Electron.app" / "Contents" / "MacOS" / "Electron"
    elif system == "Linux":
        arch = "arm64" if machine == "aarch64" else "x64"
        zipname = f"electron-v{ELECTRON_VERSION}-linux-{arch}.zip"
        binary = ELECTRON_DIR / "electron"
    elif system == "Windows":
        arch = "arm64" if "ARM" in machine.upper() else "x64"
        zipname = f"electron-v{ELECTRON_VERSION}-win32-{arch}.zip"
        binary = ELECTRON_DIR / "electron.exe"
    else:
        return None

    # Already downloaded?
    if binary.is_file():
        return str(binary)

    # Check for dev install (npm-based, e.g. from source checkout)
    for dev_path in [
        Path(__file__).resolve().parent.parent / "electron" / "node_modules" / ".bin" / "electron",
        Path(__file__).resolve().parent / "electron" / "node_modules" / ".bin" / "electron",
    ]:
        if dev_path.is_file():
            return str(dev_path)

    # Download
    url = f"https://github.com/electron/electron/releases/download/v{ELECTRON_VERSION}/{zipname}"
    ELECTRON_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = ELECTRON_DIR / zipname

    print(f"  Downloading desktop app (~80 MB, one-time setup)...")
    try:
        urllib.request.urlretrieve(url, zip_path)
    except Exception as e:
        print(f"  Download failed: {e}", file=sys.stderr)
        zip_path.unlink(missing_ok=True)
        return None

    print(f"  Extracting...")
    try:
        if system == "Darwin":
            # Use ditto to preserve macOS symlinks and frameworks
            import subprocess as _sp
            _sp.run(
                ["ditto", "-xk", str(zip_path), str(ELECTRON_DIR)],
                check=True, capture_output=True,
            )
        else:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(ELECTRON_DIR)
    except Exception as e:
        print(f"  Extraction failed: {e}", file=sys.stderr)
        zip_path.unlink(missing_ok=True)
        return None
    zip_path.unlink(missing_ok=True)

    # Make binary executable on Unix
    if system != "Windows" and binary.is_file():
        binary.chmod(binary.stat().st_mode | 0o755)

    if binary.is_file():
        print(f"  Desktop app ready.")
        return str(binary)

    return None


def _brand_electron_app(binary_path: str) -> None:
    """On macOS, patch the Electron.app bundle so the dock shows 'CodeFission' with our icon."""
    import platform
    if platform.system() != "Darwin":
        return

    bp = Path(binary_path).resolve()

    # Find Electron.app/Contents/ — works for both the downloaded binary
    # (inside Electron.app/Contents/MacOS/) and npm dev installs (cli.js
    # symlink next to dist/Electron.app/).
    if "Electron.app" in str(bp):
        contents_dir = bp
        while contents_dir.name != "Electron.app":
            contents_dir = contents_dir.parent
        contents_dir = contents_dir / "Contents"
    else:
        candidate = bp.parent.parent / "electron" / "dist" / "Electron.app" / "Contents"
        if candidate.exists():
            contents_dir = candidate
        else:
            return

    plist_path = contents_dir / "Info.plist"
    if not plist_path.exists():
        return

    import plistlib, shutil

    icon_src = Path(__file__).resolve().parent / "electron" / "icon.icns"
    icon_dst = contents_dir / "Resources" / "electron.icns"
    if icon_src.exists() and icon_dst.exists():
        shutil.copy2(icon_src, icon_dst)

    with open(plist_path, "rb") as f:
        plist = plistlib.load(f)
    if plist.get("CFBundleName") != "CodeFission":
        plist["CFBundleName"] = "CodeFission"
        plist["CFBundleDisplayName"] = "CodeFission"
        with open(plist_path, "wb") as f:
            plistlib.dump(plist, f)


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


def _open_browser(url: str):
    """Open url in a Chromium-based browser, falling back to system default."""
    import platform
    import subprocess

    system = platform.system()

    if system == "Darwin":
        candidates = [
            ["open", "-a", "Google Chrome", url],
            ["open", "-a", "Brave Browser", url],
            ["open", "-a", "Microsoft Edge", url],
            ["open", "-a", "Chromium", url],
            ["open", "-a", "Vivaldi", url],
            ["open", "-a", "Opera", url],
        ]
        for cmd in candidates:
            # `open -a AppName` fails fast if the app isn't installed
            try:
                result = subprocess.run(
                    cmd, capture_output=True, timeout=3
                )
                if result.returncode == 0:
                    return
            except Exception:
                continue

    elif system == "Linux":
        bins = [
            "google-chrome", "google-chrome-stable",
            "chromium", "chromium-browser",
            "brave-browser", "microsoft-edge",
            "vivaldi", "opera",
        ]
        for b in bins:
            if shutil.which(b):
                try:
                    subprocess.Popen([b, url])
                    return
                except Exception:
                    continue

    elif system == "Windows":
        bins = [
            "chrome", "msedge", "brave", "chromium",
        ]
        for b in bins:
            if shutil.which(b):
                try:
                    subprocess.Popen([b, url])
                    return
                except Exception:
                    continue

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


def _activate_existing(existing: dict):
    """Bring the existing CodeFission instance to the foreground."""
    import platform

    mode = existing.get("mode", "browser")
    port = existing.get("port", "?")

    if mode == "desktop":
        print(f"CodeFission desktop is already running on port {port}")
        if platform.system() == "Darwin":
            import subprocess
            # Activate the Electron window via AppleScript
            subprocess.run([
                "osascript", "-e",
                'tell application "System Events" to set frontmost of '
                '(first process whose unix id is '
                f'{existing.get("electron_pid", 0)}) to true'
            ], capture_output=True)
    else:
        print(f"CodeFission is already running at http://localhost:{port}")
        _open_browser(f"http://localhost:{port}")


def _acquire_lock(port: int, mode: str = "browser"):
    existing = _read_lock()
    if existing:
        _activate_existing(existing)
        sys.exit(0)

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(json.dumps({
        "pid": os.getpid(),
        "port": port,
        "mode": mode,
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
    import logging
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--desktop", action="store_true",
        help="Launch as desktop app (Electron)",
    )
    mode.add_argument(
        "--browser", action="store_true",
        help="Open in system browser",
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

    # Resolve launch mode: --desktop, --browser, or auto-detect
    use_desktop = args.desktop or not args.browser

    actual_port = _find_available_port(args.port)
    if actual_port is None:
        print(f"Error: No available port in range {PORT_RANGE.start}-{PORT_RANGE.stop - 1}.", file=sys.stderr)
        raise SystemExit(1)

    os.environ["CODEFISSION_PORT"] = str(actual_port)

    launch_mode = "browser"
    if use_desktop:
        electron_bin = _get_electron_binary()
        if electron_bin:
            _brand_electron_app(electron_bin)
            launch_mode = "desktop"
        elif args.desktop:
            print("Error: Failed to set up Electron.", file=sys.stderr)
            raise SystemExit(1)

    _acquire_lock(actual_port, mode=launch_mode)

    if launch_mode == "desktop":
        os.environ["CODEFISSION_NO_BROWSER"] = "1"
        electron_dir = Path(__file__).resolve().parent / "electron"
        import subprocess as _sp
        proc = _sp.Popen(
            [str(electron_bin), str(electron_dir)],
            env={**os.environ, "CODEFISSION_PORT": str(actual_port)},
        )
        # Update lock with Electron PID so we can activate it later
        lock_data = json.loads(LOCK_FILE.read_text())
        lock_data["electron_pid"] = proc.pid
        LOCK_FILE.write_text(json.dumps(lock_data) + "\n")
        print(f"Desktop: CodeFission (Electron) on port {actual_port}")
    else:
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
