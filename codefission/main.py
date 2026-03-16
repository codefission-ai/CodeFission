"""FastAPI application — WebSocket endpoint, file serving, health check.

This is the HTTP/WS server. The WebSocket endpoint at /ws handles all
browser communication via ConnectionHandler. File upload/download/serving
routes are in handlers/uploads.py. The /health endpoint is used for
server discovery.

Started by server.py (the launcher).
"""

import asyncio
import logging
import os
import re
import sys
import webbrowser
from pathlib import Path

# Allow running inside a Claude Code session
os.environ.pop("CLAUDECODE", None)

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Add package dir to path so bare imports (db, handlers, etc.) resolve
sys.path.insert(0, str(Path(__file__).parent))

from db import init_db, close_db
from handlers import ConnectionHandler
from handlers.uploads import router as uploads_router
from orchestrator import Orchestrator

app = FastAPI(title="CodeFission")

# Shared orchestrator instance
_orchestrator = Orchestrator()

# Mount file upload/download/draft HTTP routes
app.include_router(uploads_router)

# Installed mode: pre-built static files bundled in package
UI_DIR = Path(__file__).parent / "static"
if not UI_DIR.exists():
    # Development mode: ui dist built from repo root
    UI_DIR = Path(__file__).parent.parent / "ui" / "dist"


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

    await init_db()

    # Auto-open browser
    async def _open_browser():
        await asyncio.sleep(0.5)
        try:
            port = int(os.environ.get("CODEFISSION_PORT", "8080"))
            webbrowser.open(f"http://localhost:{port}")
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

    # No repo context on connect — the frontend sends open_repo to set it
    handler = ConnectionHandler(
        ws,
        orchestrator=_orchestrator,
    )

    try:
        while True:
            data = await ws.receive_json()
            await handler.dispatch(data)
    except (WebSocketDisconnect, RuntimeError):
        handler.cleanup()


@app.get("/api/browse")
async def browse_directory(path: str = "~"):
    """List subdirectories for a given path. Used by the folder browser."""
    target = Path(path).expanduser().resolve()
    if not target.is_dir():
        raise HTTPException(400, "Not a directory")

    entries = []
    try:
        for item in sorted(target.iterdir()):
            if item.name.startswith("."):
                continue  # skip hidden
            if item.is_dir():
                is_git = (item / ".git").is_dir()
                entries.append({
                    "name": item.name,
                    "path": str(item),
                    "is_git": is_git,
                })
    except PermissionError:
        pass

    return {
        "current": str(target),
        "parent": str(target.parent) if target != target.parent else None,
        "is_git": (target / ".git").is_dir(),
        "entries": entries,
    }


@app.post("/api/create-empty-project")
async def create_empty_project(name: str):
    """Create an empty git repo in ~/.codefission/projects/{name}."""
    projects_dir = Path.home() / ".codefission" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    project_path = projects_dir / name
    if project_path.exists():
        raise HTTPException(400, f"Project '{name}' already exists")
    project_path.mkdir()
    from store.git import init_git_repo
    await init_git_repo(project_path)
    return {"path": str(project_path)}


@app.post("/api/clone")
async def clone_github_repo(url: str, name: str | None = None):
    """Clone a GitHub repo into ~/.codefission/projects/{name}."""
    projects_dir = Path.home() / ".codefission" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    # Derive name from URL if not provided
    if not name:
        match = re.search(r"/([^/]+?)(?:\.git)?$", url)
        name = match.group(1) if match else "cloned-repo"

    project_path = projects_dir / name
    if project_path.exists():
        raise HTTPException(400, f"Project '{name}' already exists")

    # Clone
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", url, str(project_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(400, f"Clone failed: {stderr.decode()}")

    return {"path": str(project_path), "name": name}


@app.get("/api/git-graph/{repo_path:path}")
async def git_graph(repo_path: str, limit: int = 100):
    """Return git commit graph for a repository, annotated with CodeFission entities."""
    log = logging.getLogger(__name__)
    repo = Path("/") / repo_path  # repo_path comes without leading slash
    if not repo.is_dir():
        raise HTTPException(400, f"Not a directory: {repo}")
    if not (repo / ".git").is_dir():
        raise HTTPException(400, f"Not a git repo: {repo}")

    # Run git log
    proc = await asyncio.create_subprocess_exec(
        "git", "log", "--all",
        "--format=%H|%P|%s|%an|%aI|%D",
        "--topo-order", f"-n{limit}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(repo),
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(400, f"git log failed: {stderr.decode()}")

    # Parse commits
    commits = []
    for line in stdout.decode().strip().splitlines():
        if not line:
            continue
        parts = line.split("|", 5)
        if len(parts) < 6:
            continue
        sha, parents_str, message, author, date, refs_str = parts
        parents = parents_str.split() if parents_str.strip() else []
        refs = [r.strip() for r in refs_str.split(",") if r.strip()] if refs_str.strip() else []
        commits.append({
            "sha": sha,
            "short_sha": sha[:7],
            "parents": parents,
            "message": message,
            "author": author,
            "date": date,
            "refs": refs,
            "trees": [],
            "nodes": [],
        })

    # Query DB for CodeFission trees and nodes in this repo
    repo_path_str = str(repo)
    try:
        from store.trees import list_trees
        from db import get_db as _get_db

        all_trees = await list_trees()
        trees = [t for t in all_trees if t.repo_path == repo_path_str]

        nodes_rows = []
        tree_ids = [t.id for t in trees]
        if tree_ids:
            async with _get_db() as db:
                placeholders = ",".join("?" * len(tree_ids))
                cursor = await db.execute(
                    f"SELECT id, tree_id, label, git_commit, status FROM nodes WHERE tree_id IN ({placeholders})",
                    tree_ids,
                )
                nodes_rows = await cursor.fetchall()

        # Build commit -> trees/nodes mapping
        commit_trees: dict[str, list[dict]] = {}
        commit_nodes: dict[str, list[dict]] = {}
        for t in trees:
            if t.base_commit:
                commit_trees.setdefault(t.base_commit, []).append({
                    "tree_id": t.id, "tree_name": t.name,
                })
        for n in nodes_rows:
            if n["git_commit"]:
                commit_nodes.setdefault(n["git_commit"], []).append({
                    "node_id": n["id"], "tree_id": n["tree_id"], "label": n["label"],
                })

        # Annotate commits
        for c in commits:
            c["trees"] = commit_trees.get(c["sha"], [])
            c["nodes"] = commit_nodes.get(c["sha"], [])
    except Exception:
        log.exception("Error querying CodeFission entities for git graph")
        # Return commits without annotations rather than failing

    # Extract branch names
    branches = set()
    for c in commits:
        for ref in c["refs"]:
            # Parse "HEAD -> main" or "origin/main" etc.
            if "->" in ref:
                branches.add(ref.split("->")[-1].strip())
            elif "/" not in ref or ref.startswith("origin/"):
                branches.add(ref)

    repo_name = repo.name
    return {
        "commits": commits,
        "branches": sorted(branches),
        "repo_name": repo_name,
    }


@app.get("/health")
async def health():
    """Health check endpoint for server discovery."""
    return {"status": "ok"}


# Serve frontend
if UI_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(UI_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = UI_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(UI_DIR / "index.html")
