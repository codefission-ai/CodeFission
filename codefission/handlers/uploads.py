"""HTTP routes for file upload, download, and draft management.

Extracted from main.py to keep the app entry point lean. These routes
handle binary file I/O that doesn't fit the WebSocket protocol.
"""

import io
import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse, Response, JSONResponse

from store.trees import get_node, get_tree, update_node
from store.git import (
    ensure_worktree, auto_commit,
    resolve_workspace, read_file_bytes_from_commit, read_artifact_bytes,
    _run_git,
)
from config import set_project_path, get_project_path

router = APIRouter()

# ── Helpers ──────────────────────────────────────────────────────────

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB total


def _set_context_for_tree(tree):
    """Set project path context from a tree's repo_path."""
    if tree.repo_path:
        set_project_path(Path(tree.repo_path))


# ── Upload files into a node's workspace ──────────────────────────────

@router.post("/api/trees/{tree_id}/nodes/{node_id}/upload")
async def upload_node_files(
    tree_id: str,
    node_id: str,
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(...),
):
    """Upload files into a node's workspace (additive merge)."""
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

    _set_context_for_tree(tree)

    root_id = tree.root_node_id
    if not root_id:
        raise HTTPException(400, "Tree has no root node")

    ws_path = await ensure_worktree(
        root_id, node_id,
        node.parent_id, node.git_commit,
    )

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

@router.delete("/api/trees/{tree_id}/nodes/{node_id}/files/{file_path:path}")
async def delete_node_file(tree_id: str, node_id: str, file_path: str):
    """Remove a file from a node's workspace and commit the deletion."""
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

@router.post("/api/trees/{tree_id}/nodes/{parent_id}/prepare-draft")
async def prepare_draft(tree_id: str, parent_id: str):
    """Create a draft child node with workspace ready for file uploads."""
    from orchestrator import Orchestrator

    node = await get_node(parent_id)
    if not node:
        raise HTTPException(404, "Parent node not found")
    tree = await get_tree(tree_id)
    if not tree or tree.id != node.tree_id:
        raise HTTPException(404, "Tree not found")

    _set_context_for_tree(tree)

    # Use the module-level orchestrator from main
    from main import _orchestrator
    draft = await _orchestrator.prepare_draft(parent_id)
    return JSONResponse({"draft_node_id": draft.id})


@router.delete("/api/trees/{tree_id}/drafts/{draft_id}")
async def discard_draft(tree_id: str, draft_id: str):
    """Delete a draft node and its workspace."""
    tree = await get_tree(tree_id)
    if tree:
        _set_context_for_tree(tree)

    from main import _orchestrator
    await _orchestrator.discard_draft(tree_id, draft_id)
    return JSONResponse({"ok": True})


# ── Raw file serving for binary content (images, video, audio) ──────

@router.get("/api/files/{node_id}/{file_path:path}")
async def serve_node_file(node_id: str, file_path: str):
    node = await get_node(node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    tree = await get_tree(node.tree_id)
    if not tree:
        raise HTTPException(404, "Tree not found")

    _set_context_for_tree(tree)

    ws_path = resolve_workspace(tree.root_node_id, node_id)
    ws_resolved = str(ws_path.resolve())
    abs_candidate = "/" + file_path
    if abs_candidate.startswith(ws_resolved + "/"):
        file_path = abs_candidate[len(ws_resolved) + 1:]

    resolved = (ws_path / file_path).resolve()
    if str(resolved).startswith(ws_resolved) and resolved.is_file():
        return FileResponse(resolved)

    artifact_data = read_artifact_bytes(node_id, file_path)
    if artifact_data is not None:
        mime, _ = mimetypes.guess_type(file_path)
        return Response(content=artifact_data, media_type=mime or "application/octet-stream")

    if node.git_commit:
        try:
            raw = await read_file_bytes_from_commit(node.git_commit, file_path)
            mime, _ = mimetypes.guess_type(file_path)
            return Response(content=raw, media_type=mime or "application/octet-stream")
        except Exception:
            pass

    raise HTTPException(404, "File not found")


# ── Download file with Content-Disposition: attachment ───────────────

@router.get("/api/download/{node_id}/{file_path:path}")
async def download_node_file(node_id: str, file_path: str):
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

    artifact_data = read_artifact_bytes(node_id, file_path)
    if artifact_data is not None:
        filename = Path(file_path).name
        return Response(
            content=artifact_data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

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

@router.get("/api/download-zip/{node_id}")
async def download_node_zip(node_id: str, subpath: str = ""):
    """Zip and download a subfolder (or entire workspace) for a node."""
    import asyncio
    import zipfile

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

    target = (ws_path / subpath).resolve() if subpath else ws_path.resolve()
    if str(target).startswith(str(ws_path.resolve())) and target.is_dir():
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

    if node.git_commit:
        try:
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
