"""CLI entry point for CodeFission."""

import argparse
import atexit
import json
import os
import shutil
import socket
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import uvicorn

# Compute DATA_DIR locally to avoid importing from codefission.config
# (bare import `from config import ...` fails when run as installed entry point)
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
            "\n      Windows: https://git-scm.com/download/win"
        )
    if not shutil.which("claude"):
        missing.append(
            "Claude Code CLI - install with: npm install -g @anthropic-ai/claude-code"
            "\n      Then authenticate: claude login"
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
    """Find an available port, preferring the given one."""
    if _is_port_available(preferred):
        return preferred
    for port in PORT_RANGE:
        if port != preferred and _is_port_available(port):
            return port
    return None


def _detect_git_root(path: Path) -> Path | None:
    """Run git rev-parse --show-toplevel to find the git root. Returns None if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return None


def _auto_init_repo(path: Path):
    """Initialize a git repo, add all files, and make an initial commit."""
    print(f"Initializing git in {path} ...")
    subprocess.run(["git", "init"], cwd=str(path), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(path), check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit", "--allow-empty"],
        cwd=str(path),
        check=True,
        env={**os.environ, "GIT_COMMITTER_NAME": "CodeFission", "GIT_COMMITTER_EMAIL": "codefission@local",
             "GIT_AUTHOR_NAME": "CodeFission", "GIT_AUTHOR_EMAIL": "codefission@local"},
    )


def _ensure_gitignore(project_path: Path):
    """Ensure .codefission/ is in the project's .gitignore."""
    gitignore = project_path / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        if ".codefission/" not in content:
            with open(gitignore, "a") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(".codefission/\n")
    else:
        gitignore.write_text(".codefission/\n")


def _compute_repo_id(repo_path: Path) -> str:
    """Compute repo identity: SHA of the initial commit."""
    result = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=str(repo_path),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to compute repo_id: {result.stderr}")
    return result.stdout.strip().splitlines()[0]


def _get_head_commit(repo_path: Path) -> str:
    """Get HEAD commit SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_path),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get HEAD: {result.stderr}")
    return result.stdout.strip()


# ── Server lock ──────────────────────────────────────────────────────


def _read_lock() -> dict | None:
    """Read server lock. Returns None if no valid lock exists."""
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


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _acquire_lock(port: int, repo_path: Path | None = None,
                  repo_id: str | None = None, head_commit: str | None = None):
    """Write server lock. If another instance is alive, open browser to it and exit."""
    existing = _read_lock()
    if existing:
        existing_port = existing.get("port", "?")
        url = f"http://localhost:{existing_port}"
        if repo_id and head_commit and repo_path:
            url += f"?repo_id={repo_id}&head={head_commit}&path={quote(str(repo_path), safe='/')}"
        print(f"CodeFission is already running at http://localhost:{existing_port}")
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
    _check_prerequisites()

    parser = argparse.ArgumentParser(
        prog="fission",
        description="CodeFission — tree-structured AI development",
    )
    parser.add_argument("path", nargs="?", default=os.getcwd(), help="Project directory (defaults to current directory)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Server port (default: {DEFAULT_PORT})")
    args = parser.parse_args()

    target_path = Path(args.path).resolve()

    if not target_path.is_dir():
        print(f"Error: {target_path} is not a directory.")
        sys.exit(1)

    # Detect git repo
    repo_path = None
    repo_id = None
    head_commit = None

    is_home = target_path == Path.home()

    if not is_home:
        git_root = _detect_git_root(target_path)
        if git_root:
            repo_path = git_root
        else:
            # Interactive prompt before auto-init
            if sys.stdin.isatty():
                answer = input("This directory is not a git repo. Initialize one? [Y/n] ").strip().lower()
                if answer and answer not in ("y", "yes"):
                    print("Aborted.")
                    sys.exit(0)
            else:
                print("Error: Not a git repo and not running interactively. Initialize git first.")
                sys.exit(1)
            _auto_init_repo(target_path)
            repo_path = target_path

        # Compute repo identity
        repo_id = _compute_repo_id(repo_path)
        head_commit = _get_head_commit(repo_path)
        _ensure_gitignore(repo_path)

    # Find an available port
    port = _find_available_port(args.port)
    if port is None:
        print(f"Error: No available port in range {PORT_RANGE.start}-{PORT_RANGE.stop - 1}.")
        sys.exit(1)

    # Acquire server lock (opens browser if already running)
    _acquire_lock(port, repo_path, repo_id, head_commit)

    # Set env vars for the server
    if repo_path:
        os.environ["CODEFISSION_REPO_PATH"] = str(repo_path)
        os.environ["CODEFISSION_REPO_ID"] = repo_id
        os.environ["CODEFISSION_HEAD_COMMIT"] = head_commit
    os.environ["CODEFISSION_PORT"] = str(port)

    if repo_path:
        print(f"Repo:    {repo_path}")
    else:
        print("No repo context (home directory mode)")
    print(f"Server:  http://localhost:{port}")

    uvicorn.run(
        "codefission.main:app",
        host="0.0.0.0",
        port=port,
        ws_ping_interval=30,
        ws_ping_timeout=10,
    )


if __name__ == "__main__":
    main()
