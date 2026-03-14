import asyncio
import os
import sys
import webbrowser

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
from handlers import ConnectionHandler, list_providers
from services.orchestrator import Orchestrator

app = FastAPI(title="CodeFission")

# Shared orchestrator instance — used by both WS handler and REST routes
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


# ── REST API (CLI Presenter) ─────────────────────────────────────────
#
# These routes call the same Orchestrator methods as the WS handler.
# They serve as the CLI's backend and can also be used by other HTTP clients.

from pydantic import BaseModel as PydanticModel
from typing import Optional


class CreateTreeRequest(PydanticModel):
    name: str = "Untitled"
    base_branch: str = "main"
    repo_id: Optional[str] = None
    repo_path: Optional[str] = None
    repo_name: Optional[str] = None


class PatchTreeRequest(PydanticModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    max_turns: Optional[int] = None
    skill: Optional[str] = None
    notes: Optional[str] = None


class BranchRequest(PydanticModel):
    label: str = ""
    created_by: str = "human"


class ChatRequest(PydanticModel):
    message: str
    after_id: Optional[str] = None
    file_quotes: Optional[list] = None
    draft_node_id: Optional[str] = None
    created_by: str = "human"


class PatchSettingsRequest(PydanticModel):
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    default_max_turns: Optional[str] = None
    auth_mode: Optional[str] = None
    api_key: Optional[str] = None
    summary_model: Optional[str] = None
    data_dir: Optional[str] = None


@app.get("/health")
async def health():
    """Health check endpoint for CLI server discovery."""
    return {"status": "ok"}


@app.post("/api/trees", status_code=201)
async def api_create_tree(req: CreateTreeRequest):
    """Create a new tree."""
    from services.workspace import detect_repo_name as _detect_repo_name

    if req.repo_path:
        set_project_path(Path(req.repo_path))

    try:
        tree, root = await _orchestrator.create_tree(
            req.name,
            base_branch=req.base_branch,
            repo_id=req.repo_id,
            repo_path=req.repo_path,
            repo_name=req.repo_name,
        )
        return {"tree": tree.model_dump(), "root": root.model_dump()}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/trees")
async def api_list_trees():
    """List all trees."""
    from services.trees import list_trees
    trees = await list_trees()
    return {"trees": [t.model_dump() for t in trees]}


@app.delete("/api/trees/{tree_id}")
async def api_delete_tree(tree_id: str):
    """Delete a tree."""
    from services.trees import get_tree, delete_tree
    tree = await get_tree(tree_id)
    if not tree:
        raise HTTPException(404, "Tree not found")
    if tree.repo_path:
        set_project_path(Path(tree.repo_path))
    await delete_tree(tree_id)
    return {"ok": True}


@app.patch("/api/trees/{tree_id}")
async def api_patch_tree(tree_id: str, req: PatchTreeRequest):
    """Update tree settings."""
    from services.trees import get_tree
    tree = await get_tree(tree_id)
    if not tree:
        raise HTTPException(404, "Tree not found")

    data = req.dict(exclude_none=True)
    if not data:
        return {"tree": tree.model_dump()}

    # Handle name separately (not a "setting")
    if "name" in data:
        from services.trees import update_tree
        await update_tree(tree_id, name=data.pop("name"))

    if data:
        await _orchestrator.update_tree_settings(tree_id, data)

    updated = await get_tree(tree_id)
    return {"tree": updated.model_dump()}


@app.post("/api/trees/{tree_id}/nodes/{node_id}/branch", status_code=201)
async def api_branch(tree_id: str, node_id: str, req: BranchRequest):
    """Create a branch from a node."""
    from services.trees import get_node
    node = await get_node(node_id)
    if not node or node.tree_id != tree_id:
        raise HTTPException(404, "Node not found in this tree")
    child = await _orchestrator.branch(node_id, req.label, created_by=req.created_by)
    return {"node": child.model_dump()}


@app.delete("/api/trees/{tree_id}/nodes/{node_id}")
async def api_delete_node(tree_id: str, node_id: str):
    """Delete a node and its subtree."""
    from services.trees import get_node
    node = await get_node(node_id)
    if not node or node.tree_id != tree_id:
        raise HTTPException(404, "Node not found in this tree")

    # Set project context
    from services.trees import get_tree
    tree = await get_tree(tree_id)
    if tree and tree.repo_path:
        set_project_path(Path(tree.repo_path))

    try:
        result = await _orchestrator.delete_node(node_id)
        return {
            "deleted_ids": result.deleted_ids,
            "updated_nodes": [n.model_dump() for n in result.updated_nodes],
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/trees/{tree_id}/nodes/{node_id}/chat")
async def api_chat(tree_id: str, node_id: str, req: ChatRequest):
    """Stream a chat response via Server-Sent Events."""
    import json as json_mod
    from services.trees import get_node, get_tree
    from services.orchestrator import ChatNodeCreated, ChatCompleted
    from services.chat import TextDelta, ToolStart, ToolEnd, SessionInit

    node = await get_node(node_id)
    if not node or node.tree_id != tree_id:
        raise HTTPException(404, "Node not found in this tree")

    tree = await get_tree(tree_id)
    if tree and tree.repo_path:
        set_project_path(Path(tree.repo_path))

    def _serialize_event(event) -> dict:
        """Serialize a domain event for SSE."""
        if isinstance(event, ChatNodeCreated):
            d = {"type": "node_created", "node": event.node.model_dump()}
            if event.after_id:
                d["after_id"] = event.after_id
            return d
        elif isinstance(event, SessionInit):
            return {"type": "session_init", "session_id": event.session_id}
        elif isinstance(event, TextDelta):
            return {"type": "text_delta", "text": event.text}
        elif isinstance(event, ToolStart):
            return {
                "type": "tool_start",
                "tool_call_id": event.tool_call_id,
                "name": event.name,
                "arguments": getattr(event, "arguments", None),
            }
        elif isinstance(event, ToolEnd):
            return {
                "type": "tool_end",
                "tool_call_id": event.tool_call_id,
                "name": event.name,
                "result": event.result,
                "is_error": event.is_error,
            }
        elif isinstance(event, ChatCompleted):
            return {
                "type": "done",
                "node_id": event.result.node_id,
                "full_response": event.result.full_response,
                "git_commit": event.result.git_commit,
                "files_changed": event.result.files_changed,
            }
        return {"type": "unknown"}

    async def event_stream():
        try:
            async for event in _orchestrator.chat(
                node_id, req.message,
                after_id=req.after_id,
                file_quotes=req.file_quotes,
                draft_node_id=req.draft_node_id,
                created_by=req.created_by,
            ):
                data = json_mod.dumps(_serialize_event(event))
                yield f"data: {data}\n\n"
        except Exception as e:
            error_data = json_mod.dumps({"type": "error", "error": str(e)})
            yield f"data: {error_data}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/trees/{tree_id}/nodes/{node_id}/cancel")
async def api_cancel(tree_id: str, node_id: str):
    """Cancel an active chat stream."""
    from handlers import _active_streams
    from services.process_service import kill_process_tree

    info = _active_streams.get(node_id)
    if info:
        info.cancelled = True
        if info.sdk_pid:
            kill_process_tree(info.sdk_pid)
            info.sdk_pid = None
        if info.stream_task and not info.stream_task.done():
            info.stream_task.cancel()
        return {"ok": True}
    else:
        raise HTTPException(404, "No active stream for this node")


@app.get("/api/trees/{tree_id}/nodes/{node_id}")
async def api_get_node(tree_id: str, node_id: str):
    """Get a single node's data."""
    from services.trees import get_node
    node = await get_node(node_id)
    if not node or node.tree_id != tree_id:
        raise HTTPException(404, "Node not found")
    return {"node": node.model_dump()}


@app.get("/api/trees/{tree_id}/nodes/{node_id}/files")
async def api_get_node_files(tree_id: str, node_id: str):
    """List files for a node."""
    from services.trees import get_node, get_tree
    node = await get_node(node_id)
    if not node or node.tree_id != tree_id:
        raise HTTPException(404, "Node not found")
    tree = await get_tree(tree_id)
    if tree and tree.repo_path:
        set_project_path(Path(tree.repo_path))
    try:
        result = await _orchestrator.list_node_files(node_id)
        return {"node_id": result.node_id, "files": result.files}
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/trees/{tree_id}/nodes/{node_id}/diff")
async def api_get_node_diff(tree_id: str, node_id: str):
    """Get diff for a node."""
    from services.trees import get_node, get_tree
    node = await get_node(node_id)
    if not node or node.tree_id != tree_id:
        raise HTTPException(404, "Node not found")
    tree = await get_tree(tree_id)
    if tree and tree.repo_path:
        set_project_path(Path(tree.repo_path))
    try:
        result = await _orchestrator.get_node_diff(node_id)
        return {"node_id": result.node_id, "diff": result.diff}
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/trees/{tree_id}/log")
async def api_get_log(tree_id: str, limit: int = 100):
    """Get audit log for a tree."""
    from services.actions import ActionLog
    log = ActionLog()
    actions = await log.list_actions(tree_id, limit=limit)
    return {"actions": [
        {
            "id": a.id,
            "seq": a.seq,
            "ts": a.ts,
            "tree_id": a.tree_id,
            "node_id": a.node_id,
            "kind": a.kind,
            "params": a.params,
            "result": a.result,
            "source": a.source,
        }
        for a in actions
    ]}


@app.get("/api/settings")
async def api_get_settings():
    """Get global settings."""
    from services.trees import get_global_defaults
    defaults = await get_global_defaults()
    return {"global_defaults": defaults, "providers": list_providers()}


@app.patch("/api/settings")
async def api_patch_settings(req: PatchSettingsRequest):
    """Update global settings."""
    data = req.dict(exclude_none=True)
    defaults = await _orchestrator.update_global_settings(data)
    return {"global_defaults": defaults, "providers": list_providers()}


@app.get("/api/providers")
async def api_get_providers():
    """List available providers."""
    return {"providers": list_providers()}


# Serve frontend
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = FRONTEND_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
