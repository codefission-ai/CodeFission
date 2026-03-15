"""FastAPI application — WebSocket endpoint, file serving, health check.

This is the HTTP/WS server. The WebSocket endpoint at /ws handles all
browser communication via ConnectionHandler. File upload/download/serving
routes are in handlers/uploads.py. The /health endpoint is used for
server discovery.

Started by server.py (the launcher).
"""

import asyncio
import os
import sys
import webbrowser
from pathlib import Path

# Allow running inside a Claude Code session
os.environ.pop("CLAUDECODE", None)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Add package dir to path so bare imports (db, handlers, etc.) resolve
sys.path.insert(0, str(Path(__file__).parent))

from config import set_project_path
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
