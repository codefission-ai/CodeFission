import asyncio
import os
import sys

# Allow running inside a Claude Code session
os.environ.pop("CLAUDECODE", None)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
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
    ws_resolved = str(ws_path.resolve())
    # If file_path is an absolute workspace path the model embedded, extract the relative part
    abs_candidate = "/" + file_path
    if abs_candidate.startswith(ws_resolved + "/"):
        file_path = abs_candidate[len(ws_resolved) + 1:]
    resolved = (ws_path / file_path).resolve()
    if not str(resolved).startswith(ws_resolved):
        raise HTTPException(403, "Path traversal detected")
    if not resolved.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(resolved)


# ── Download file with Content-Disposition: attachment ───────────────
@app.get("/api/download/{node_id}/{file_path:path}")
async def download_node_file(node_id: str, file_path: str):
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
    return FileResponse(resolved, filename=resolved.name,
                        media_type="application/octet-stream")


# ── Download folder or entire workspace as zip ──────────────────────
@app.get("/api/download-zip/{node_id}")
async def download_node_zip(node_id: str, subpath: str = ""):
    """Zip and download a subfolder (or entire workspace) for a node."""
    import io
    import zipfile
    from services.tree_service import get_node, get_tree
    from services.workspace_service import resolve_workspace

    node = await get_node(node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    tree = await get_tree(node.tree_id)
    if not tree:
        raise HTTPException(404, "Tree not found")

    ws_path = resolve_workspace(tree.id, tree.root_node_id, node_id)
    target = (ws_path / subpath).resolve() if subpath else ws_path.resolve()
    if not str(target).startswith(str(ws_path.resolve())):
        raise HTTPException(403, "Path traversal detected")
    if not target.is_dir():
        raise HTTPException(404, "Directory not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in sorted(target.rglob("*")):
            if fpath.is_file() and ".git" not in fpath.parts:
                arcname = str(fpath.relative_to(target))
                zf.write(fpath, arcname)
    buf.seek(0)

    import re
    raw_name = subpath.replace("/", "_") or node.label or node_id
    zip_name = re.sub(r"[^a-z0-9]+", "_", raw_name.lower()).strip("_") + ".zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


# Serve frontend
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = FRONTEND_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
