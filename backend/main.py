import asyncio
import os
import sys

# Allow running inside a Claude Code session
os.environ.pop("CLAUDECODE", None)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

# Add backend to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from db import init_db, close_db
from handlers import ConnectionHandler

app = FastAPI(title="RepoEvolve")

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
    await init_db()


@app.on_event("shutdown")
async def shutdown():
    await close_db()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    handler = ConnectionHandler(ws)

    try:
        while True:
            data = await ws.receive_json()
            await handler.dispatch(data)
    except WebSocketDisconnect:
        handler.cleanup()


# ── Raw file serving for binary content (images, video, audio) ──────
@app.get("/api/files/{node_id}/{file_path:path}")
async def serve_node_file(node_id: str, file_path: str):
    from services.tree_service import get_node, get_tree
    from services.workspace_service import resolve_workspace

    node = await get_node(node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    tree = await get_tree(node.tree_id)
    if not tree:
        raise HTTPException(404, "Tree not found")
    ws_path = resolve_workspace(tree.id, tree.root_node_id, node_id)
    resolved = (ws_path / file_path).resolve()
    if not str(resolved).startswith(str(ws_path.resolve())):
        raise HTTPException(403, "Path traversal detected")
    if not resolved.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(resolved)


# Serve frontend
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = FRONTEND_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
