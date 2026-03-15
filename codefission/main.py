import argparse
import asyncio
import atexit
import json
import os
import shutil
import socket
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from urllib.parse import quote

# Allow running inside a Claude Code session
os.environ.pop("CLAUDECODE", None)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, Response, JSONResponse
from pathlib import Path

# Add package dir to path so bare imports (db, handlers, etc.) resolve
sys.path.insert(0, str(Path(__file__).parent))

from config import set_project_path
from db import init_db, close_db
from handlers import ConnectionHandler
from services.orchestrator import Orchestrator

app = FastAPI(title="CodeFission")

# Shared orchestrator instance
_orchestrator = Orchestrator()


def _set_context_for_tree(tree):
    """Set project path context from a tree's repo_path, falling back to env var."""
    if tree.repo_path:
        set_project_path(Path(tree.repo_path))
    elif os.environ.get("CODEFISSION_REPO_PATH"):
        set_project_path(Path(os.environ["CODEFISSION_REPO_PATH"]))

# Installed mode: pre-built static files bundled in package
FRONTEND_DIR = Path(__file__).parent / "static"
if not FRONTEND_DIR.exists():
    # Development mode: frontend dist built from repo root
    FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"


def _silence_asyncgen_gc(loop, context):
    """Suppress RuntimeError from anyio cancel scopes during GC of SDK generators."""
    exc = context.get("exception")
    if isinstance(exc, RuntimeError) and "cancel scope" in str(exc):
        return
    loop.default_exception_handler(context)


@app.on_event("startup")
async def startup():
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_silence_asyncgen_gc)

    # Init global DB unconditionally
    await init_db()

    # If launched from a repo, set project context and run lazy migration
    repo_path_str = os.environ.get("CODEFISSION_REPO_PATH")
    repo_id = os.environ.get("CODEFISSION_REPO_ID")
    if repo_path_str:
        set_project_path(Path(repo_path_str))

    # Auto-open browser after server is listening
    async def _open_browser():
        await asyncio.sleep(0.5)
        try:
            port = int(os.environ.get("CODEFISSION_PORT", "8080"))
            url = f"http://localhost:{port}"
            if repo_path_str and repo_id:
                from urllib.parse import quote
                head_commit = os.environ.get("CODEFISSION_HEAD_COMMIT", "")
                url += f"?repo_id={repo_id}&head={head_commit}&path={quote(repo_path_str, safe='/')}"
            webbrowser.open(url)
        except Exception:
            pass
    asyncio.create_task(_open_browser())


@app.on_event("shutdown")
async def shutdown():
    await close_db()
    # Clean up server lock file
    lock = Path.home() / ".codefission" / "server.lock"
    try:
        lock.unlink(missing_ok=True)
    except Exception:
        pass


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    # Set project context for this connection from env var
    repo_path_str = os.environ.get("CODEFISSION_REPO_PATH")
    repo_id = os.environ.get("CODEFISSION_REPO_ID")
    head_commit = os.environ.get("CODEFISSION_HEAD_COMMIT")

    if repo_path_str:
        set_project_path(Path(repo_path_str))

    handler = ConnectionHandler(
        ws,
        repo_path=repo_path_str,
        repo_id=repo_id,
        head_commit=head_commit,
        orchestrator=_orchestrator,
    )

    try:
        while True:
            data = await ws.receive_json()
            await handler.dispatch(data)
    except (WebSocketDisconnect, RuntimeError):
        handler.cleanup()


# ── Upload files into a node's workspace ──────────────────────────────
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB total


@app.post("/api/trees/{tree_id}/nodes/{node_id}/upload")
async def upload_node_files(
    tree_id: str,
    node_id: str,
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(...),
):
    """Upload files into a node's workspace (additive merge)."""
    from services.trees import get_node, get_tree, update_node
    from services.workspace import (
        ensure_worktree, auto_commit,
    )

    if len(files) != len(paths):
        raise HTTPException(400, "files and paths must have same length")
    if not files:
        raise HTTPException(400, "No files provided")

    node = await get_node(node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    tree = await get_tree(tree_id)
    if not tree or tree.id != node.tree_id:
        raise HTTPException(404, "Tree not found")

    # Set context for this tree
    _set_context_for_tree(tree)

    root_id = tree.root_node_id
    if not root_id:
        raise HTTPException(400, "Tree has no root node")

    # Ensure workspace exists
    ws_path = await ensure_worktree(
        root_id, node_id,
        node.parent_id, node.git_commit,
    )

    # Write files (additive, overwrite same-name)
    ws_resolved = ws_path.resolve()
    total_bytes = 0
    written = []
    for upload_file, rel_path in zip(files, paths):
        rel = Path(rel_path)
        if rel.is_absolute() or ".." in rel.parts:
            continue
        dest = (ws_path / rel).resolve()
        if not str(dest).startswith(str(ws_resolved)):
            continue

        content = await upload_file.read()
        total_bytes += len(content)
        if total_bytes > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"Total upload exceeds {MAX_UPLOAD_BYTES // (1024*1024)}MB limit")

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        written.append(str(rel))

    if not written:
        raise HTTPException(400, "No valid files written")

    sha, _ = await auto_commit(ws_path, f"uploaded {len(written)} file(s)")
    await update_node(node_id, git_commit=sha)

    return JSONResponse({
        "files_written": written,
        "git_commit": sha,
        "count": len(written),
    })


# ── Delete a file from a node's workspace ─────────────────────────────

@app.delete("/api/trees/{tree_id}/nodes/{node_id}/files/{file_path:path}")
async def delete_node_file(tree_id: str, node_id: str, file_path: str):
    """Remove a file from a node's workspace and commit the deletion."""
    from services.trees import get_node, get_tree, update_node
    from services.workspace import resolve_workspace, auto_commit

    node = await get_node(node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    tree = await get_tree(tree_id)
    if not tree or tree.id != node.tree_id:
        raise HTTPException(404, "Tree not found")

    _set_context_for_tree(tree)

    ws_path = resolve_workspace(tree.root_node_id, node_id)
    rel = Path(file_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise HTTPException(400, "Invalid path")
    target = (ws_path / rel).resolve()
    if not str(target).startswith(str(ws_path.resolve())):
        raise HTTPException(400, "Invalid path")
    if not target.is_file():
        raise HTTPException(404, "File not found")

    target.unlink()
    sha, _ = await auto_commit(ws_path, f"removed {file_path}")
    await update_node(node_id, git_commit=sha)
    return JSONResponse({"ok": True, "git_commit": sha})


# ── Draft nodes for eager file upload ────────────────────────────────

@app.post("/api/trees/{tree_id}/nodes/{parent_id}/prepare-draft")
async def prepare_draft(tree_id: str, parent_id: str):
    """Create a draft child node with workspace ready for file uploads."""
    from services.trees import get_node, get_tree

    node = await get_node(parent_id)
    if not node:
        raise HTTPException(404, "Parent node not found")
    tree = await get_tree(tree_id)
    if not tree or tree.id != node.tree_id:
        raise HTTPException(404, "Tree not found")

    _set_context_for_tree(tree)

    draft = await _orchestrator.prepare_draft(parent_id)
    return JSONResponse({"draft_node_id": draft.id})


@app.delete("/api/trees/{tree_id}/drafts/{draft_id}")
async def discard_draft(tree_id: str, draft_id: str):
    """Delete a draft node and its workspace."""
    from services.trees import get_tree

    tree = await get_tree(tree_id)
    if tree:
        _set_context_for_tree(tree)

    await _orchestrator.discard_draft(tree_id, draft_id)
    return JSONResponse({"ok": True})


# ── Raw file serving for binary content (images, video, audio) ──────
@app.get("/api/files/{node_id}/{file_path:path}")
async def serve_node_file(node_id: str, file_path: str):
    from services.trees import get_node, get_tree
    from services.workspace import resolve_workspace, read_file_bytes_from_commit, read_artifact_bytes

    node = await get_node(node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    tree = await get_tree(node.tree_id)
    if not tree:
        raise HTTPException(404, "Tree not found")

    _set_context_for_tree(tree)

    ws_path = resolve_workspace(tree.root_node_id, node_id)
    ws_resolved = str(ws_path.resolve())
    # If file_path is an absolute workspace path the model embedded, extract the relative part
    abs_candidate = "/" + file_path
    if abs_candidate.startswith(ws_resolved + "/"):
        file_path = abs_candidate[len(ws_resolved) + 1:]

    # Try filesystem first (worktree alive)
    resolved = (ws_path / file_path).resolve()
    if str(resolved).startswith(ws_resolved) and resolved.is_file():
        return FileResponse(resolved)

    # Try persisted artifacts (survives worktree removal)
    artifact_data = read_artifact_bytes(node_id, file_path)
    if artifact_data is not None:
        import mimetypes
        mime, _ = mimetypes.guess_type(file_path)
        return Response(content=artifact_data, media_type=mime or "application/octet-stream")

    # Fall back to git (raw bytes to preserve binary files)
    if node.git_commit:
        try:
            raw = await read_file_bytes_from_commit(node.git_commit, file_path)
            import mimetypes
            mime, _ = mimetypes.guess_type(file_path)
            return Response(content=raw, media_type=mime or "application/octet-stream")
        except Exception:
            pass

    raise HTTPException(404, "File not found")


# ── Download file with Content-Disposition: attachment ───────────────
@app.get("/api/download/{node_id}/{file_path:path}")
async def download_node_file(node_id: str, file_path: str):
    from services.trees import get_node, get_tree
    from services.workspace import resolve_workspace, read_file_bytes_from_commit, read_artifact_bytes

    node = await get_node(node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    tree = await get_tree(node.tree_id)
    if not tree:
        raise HTTPException(404, "Tree not found")

    _set_context_for_tree(tree)

    ws_path = resolve_workspace(tree.root_node_id, node_id)
    resolved = (ws_path / file_path).resolve()
    if str(resolved).startswith(str(ws_path.resolve())) and resolved.is_file():
        return FileResponse(resolved, filename=resolved.name,
                            media_type="application/octet-stream")

    # Try persisted artifacts
    artifact_data = read_artifact_bytes(node_id, file_path)
    if artifact_data is not None:
        filename = Path(file_path).name
        return Response(
            content=artifact_data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # Fall back to git (raw bytes to preserve binary files)
    if node.git_commit:
        try:
            raw = await read_file_bytes_from_commit(node.git_commit, file_path)
            filename = Path(file_path).name
            return Response(
                content=raw,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except Exception:
            pass

    raise HTTPException(404, "File not found")


# ── Download folder or entire workspace as zip ──────────────────────
@app.get("/api/download-zip/{node_id}")
async def download_node_zip(node_id: str, subpath: str = ""):
    """Zip and download a subfolder (or entire workspace) for a node."""
    import io
    import re
    from services.trees import get_node, get_tree
    from services.workspace import resolve_workspace, _run_git
    from config import get_project_path

    node = await get_node(node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    tree = await get_tree(node.tree_id)
    if not tree:
        raise HTTPException(404, "Tree not found")

    _set_context_for_tree(tree)

    ws_path = resolve_workspace(tree.root_node_id, node_id)
    raw_name = subpath.replace("/", "_") or node.label or node_id
    zip_name = re.sub(r"[^a-z0-9]+", "_", raw_name.lower()).strip("_") + ".zip"

    # Try filesystem first
    target = (ws_path / subpath).resolve() if subpath else ws_path.resolve()
    if str(target).startswith(str(ws_path.resolve())) and target.is_dir():
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath in sorted(target.rglob("*")):
                if fpath.is_file() and ".git" not in fpath.parts:
                    arcname = str(fpath.relative_to(target))
                    zf.write(fpath, arcname)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
        )

    # Fall back to git archive
    if node.git_commit:
        try:
            import asyncio
            proc = await asyncio.create_subprocess_exec(
                "git", "archive", "--format=zip", node.git_commit,
                cwd=str(get_project_path()),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return StreamingResponse(
                    io.BytesIO(stdout),
                    media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
                )
        except Exception:
            pass

    raise HTTPException(404, "Directory not found")


@app.get("/health")
async def health():
    """Health check endpoint for server discovery."""
    return {"status": "ok"}


# Serve frontend
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = FRONTEND_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")


# ── Server launcher helpers ──────────────────────────────────────────

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
    if _is_port_available(preferred):
        return preferred
    for port in PORT_RANGE:
        if port != preferred and _is_port_available(port):
            return port
    return None


def _detect_git_root(path: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path), capture_output=True, text=True,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return None


def _auto_init_repo(path: Path):
    print(f"Initializing git in {path} ...")
    subprocess.run(["git", "init"], cwd=str(path), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(path), check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit", "--allow-empty"],
        cwd=str(path), check=True,
        env={
            **os.environ,
            "GIT_COMMITTER_NAME": "CodeFission",
            "GIT_COMMITTER_EMAIL": "codefission@local",
            "GIT_AUTHOR_NAME": "CodeFission",
            "GIT_AUTHOR_EMAIL": "codefission@local",
        },
    )


def _ensure_gitignore(project_path: Path):
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
    result = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=str(repo_path), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to compute repo_id: {result.stderr}")
    return result.stdout.strip().splitlines()[0]


def _get_head_commit(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_path), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get HEAD: {result.stderr}")
    return result.stdout.strip()


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


def _acquire_lock(port: int, repo_path: Path | None = None,
                  repo_id: str | None = None, head_commit: str | None = None):
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


# ── Entry point ──────────────────────────────────────────────────────


def main():
    """Entry point for the `fission` CLI."""
    import uvicorn

    parser = argparse.ArgumentParser(
        prog="fission",
        description="CodeFission -- tree-structured AI development.",
    )
    parser.add_argument(
        "path", nargs="?", default=".",
        help="Path to the project directory (default: current directory)",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"Server port (default: {DEFAULT_PORT})",
    )
    args = parser.parse_args()

    _check_prerequisites()

    target_path = Path(args.path).resolve()

    if not target_path.is_dir():
        print(f"Error: {target_path} is not a directory.", file=sys.stderr)
        raise SystemExit(1)

    repo_path = None
    repo_id = None
    head_commit = None
    is_home = target_path == Path.home()

    if not is_home:
        git_root = _detect_git_root(target_path)
        if git_root:
            repo_path = git_root
        else:
            if sys.stdin.isatty():
                answer = input("This directory is not a git repo. Initialize one? [Y/n] ")
                if answer.strip().lower() in ("n", "no"):
                    raise SystemExit(0)
            else:
                print("Error: Not a git repo and not running interactively.", file=sys.stderr)
                raise SystemExit(1)
            _auto_init_repo(target_path)
            repo_path = target_path

        repo_id = _compute_repo_id(repo_path)
        head_commit = _get_head_commit(repo_path)
        _ensure_gitignore(repo_path)

    actual_port = _find_available_port(args.port)
    if actual_port is None:
        print(f"Error: No available port in range {PORT_RANGE.start}-{PORT_RANGE.stop - 1}.", file=sys.stderr)
        raise SystemExit(1)

    _acquire_lock(actual_port, repo_path, repo_id, head_commit)

    if repo_path:
        os.environ["CODEFISSION_REPO_PATH"] = str(repo_path)
        os.environ["CODEFISSION_REPO_ID"] = repo_id
        os.environ["CODEFISSION_HEAD_COMMIT"] = head_commit
    os.environ["CODEFISSION_PORT"] = str(actual_port)

    if repo_path:
        print(f"Repo:    {repo_path}")
    else:
        print("No repo context (home directory mode)")
    print(f"Server:  http://localhost:{actual_port}")

    uvicorn.run(
        "codefission.main:app",
        host="0.0.0.0",
        port=actual_port,
        ws_ping_interval=30,
        ws_ping_timeout=10,
    )


if __name__ == "__main__":
    main()
