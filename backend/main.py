import asyncio
import json
import sys
import os
from dataclasses import dataclass, field

# Allow running inside a Claude Code session
os.environ.pop("CLAUDECODE", None)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

# Add backend to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from db import init_db
from events import bus, WS, STREAM_START, STREAM_DELTA, STREAM_END, STREAM_ERROR
from services.tree_service import (
    create_tree, list_trees, get_tree, get_all_nodes, get_node,
    create_child_node, update_node, update_tree, delete_tree,
)
from services.chat_service import stream_chat, TextDelta, ToolStart, ToolEnd
from services.workspace_service import (
    setup_repo, create_worktree, ensure_worktree, auto_commit,
    resolve_workspace, cleanup_tree_workspace,
    list_files, get_diff, read_file, _run_git, WORKSPACES_DIR,
)

app = FastAPI(title="RepoEvolve")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"


# ── Streaming state (per-node, backend-side) ────────────────────────────

@dataclass
class StreamState:
    node_id: str
    text: str = ""
    status: str = "active"   # active | done | error


# Active streams keyed by node_id
_streams: dict[str, StreamState] = {}


# ── WebSocket handler ───────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    await init_db()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    tasks: dict[str, asyncio.Task] = {}

    # ── Helper to send typed JSON ────────────────────────────────────
    async def send(msg_type: str, **payload):
        await ws.send_json({"type": msg_type, **payload})

    # ── Handlers (one per inbound message type) ──────────────────────

    async def handle_list_trees():
        trees = await list_trees()
        await send(WS.TREES, trees=[t.model_dump() for t in trees])

    async def handle_create_tree(data: dict):
        name = data.get("name", "Untitled")
        provider = data.get("provider", "anthropic")
        model = data.get("model", "claude-sonnet-4-6")
        tree, root = await create_tree(name, provider=provider, model=model,
                                       repo_mode="none")
        await send(WS.TREE_CREATED, tree=tree.model_dump(), root=root.model_dump())

    async def handle_load_tree(data: dict):
        tree_id = data["tree_id"]
        tree = await get_tree(tree_id)
        nodes = await get_all_nodes(tree_id)
        await send(
            WS.TREE_LOADED,
            tree=tree.model_dump() if tree else None,
            nodes=[n.model_dump() for n in nodes],
        )

    async def handle_delete_tree(data: dict):
        tree_id = data["tree_id"]
        cleanup_tree_workspace(tree_id)
        await delete_tree(tree_id)
        await send(WS.TREE_DELETED, tree_id=tree_id)

    async def handle_branch(data: dict):
        parent_id = data["parent_id"]
        label = data.get("label", "")
        node = await create_child_node(parent_id, label)

        # Set up git worktree if tree is git-enabled
        parent = await get_node(parent_id)
        if parent:
            tree = await get_tree(parent.tree_id)
            if tree and tree.repo_mode != "none":
                try:
                    await create_worktree(
                        tree.id, tree.root_node_id, node.id,
                        parent.git_commit or "HEAD",
                    )
                    branch_name = f"ct-{node.id}"
                    await update_node(node.id, git_branch=branch_name)
                    node = await get_node(node.id)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning("Worktree creation failed: %s", e)

        await send(WS.NODE_CREATED, node=node.model_dump())

    async def handle_get_node(data: dict):
        node_id = data["node_id"]
        node = await get_node(node_id)
        if node:
            await send(WS.NODE_DATA, node=node.model_dump())

    async def handle_chat(data: dict):
        node_id = data["node_id"]
        content = data["content"]

        async def _run_chat(nid: str, msg: str):
            try:
                node = await get_node(nid)
                if not node:
                    return

                tree = await get_tree(node.tree_id)
                if not tree:
                    return

                git_enabled = tree.repo_mode != "none"

                # If this node already has a completed turn, auto-create a child
                if node.assistant_response:
                    child = await create_child_node(nid, label=msg[:40])

                    if git_enabled:
                        try:
                            await create_worktree(
                                tree.id, tree.root_node_id, child.id,
                                node.git_commit or "HEAD",
                            )
                            branch_name = f"ct-{child.id}"
                            await update_node(child.id, git_branch=branch_name)
                            child = await get_node(child.id)
                        except Exception as e:
                            import logging
                            logging.getLogger(__name__).warning("Worktree creation failed: %s", e)

                    await send(WS.NODE_CREATED, node=child.model_dump())
                    nid = child.id

                # Save user message and set label
                current = await get_node(nid)
                label = current.label if current and current.label and current.label != "" else msg[:40]
                await update_node(nid, user_message=msg, label=label, status="active")
                await send(WS.NODE_DATA, node=(await get_node(nid)).model_dump())

                # Resolve workspace
                workspace = resolve_workspace(tree.id, tree.root_node_id, nid, tree.repo_mode)

                # Ensure worktree exists for git-enabled trees
                if git_enabled:
                    current = await get_node(nid)
                    parent_node = await get_node(current.parent_id) if current.parent_id else None
                    await ensure_worktree(
                        tree.id, tree.root_node_id, nid,
                        current.parent_id,
                        parent_node.git_commit if parent_node else None,
                    )

                # Init streaming state
                _streams[nid] = StreamState(node_id=nid)
                await bus.emit(STREAM_START, node_id=nid)
                await send(WS.STATUS, node_id=nid, status="active")

                # Track tool names for pairing start→end
                tool_names: dict[str, str] = {}

                # Stream response (structured events)
                async for event in stream_chat(nid, msg, workspace):
                    if isinstance(event, TextDelta):
                        _streams[nid].text += event.text
                        await bus.emit(STREAM_DELTA, node_id=nid, text=event.text)
                        await send(WS.CHUNK, node_id=nid, text=event.text)

                    elif isinstance(event, ToolStart):
                        if event.name:
                            tool_names[event.tool_call_id] = event.name
                        await send(WS.TOOL_START,
                            node_id=nid,
                            tool_call_id=event.tool_call_id,
                            name=event.name,
                            arguments=event.arguments,
                        )

                    elif isinstance(event, ToolEnd):
                        name = event.name or tool_names.get(event.tool_call_id, "")
                        await send(WS.TOOL_END,
                            node_id=nid,
                            tool_call_id=event.tool_call_id,
                            name=name,
                            result=event.result,
                            is_error=event.is_error,
                        )

                # Finalise (generator returns when SDK is done)
                full_response = _streams[nid].text
                _streams[nid].status = "done"
                await update_node(nid, assistant_response=full_response, status="done")

                # Auto-commit for git-enabled trees
                git_commit = None
                if git_enabled:
                    try:
                        commit_sha, files_changed = await auto_commit(workspace, msg)
                        await update_node(nid, git_commit=commit_sha)
                        git_commit = commit_sha
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).warning("Auto-commit failed: %s", e)

                await bus.emit(STREAM_END, node_id=nid, full_response=full_response)
                done_payload = {"node_id": nid, "full_response": full_response}
                if git_commit:
                    done_payload["git_commit"] = git_commit
                await send(WS.DONE, **done_payload)

            except Exception as e:
                import traceback
                traceback.print_exc()
                if nid in _streams:
                    _streams[nid].status = "error"
                await update_node(nid, status="error")
                await bus.emit(STREAM_ERROR, node_id=nid, error=str(e))
                await send(WS.ERROR, node_id=nid, error=str(e))

            finally:
                _streams.pop(nid, None)

        task = asyncio.create_task(_run_chat(node_id, content))
        tasks[node_id] = task

    async def handle_set_repo(data: dict):
        tree_id = data["tree_id"]
        repo_mode = data["repo_mode"]
        repo_source = data.get("repo_source")
        tree = await get_tree(tree_id)
        if not tree or not tree.root_node_id:
            await send(WS.ERROR, error="Tree not found")
            return
        try:
            await setup_repo(tree.id, tree.root_node_id, repo_mode, repo_source)
            root_dir = WORKSPACES_DIR / tree.id / tree.root_node_id
            _, head_sha, _ = await _run_git(root_dir, "rev-parse", "HEAD")
            _, branch, _ = await _run_git(root_dir, "rev-parse", "--abbrev-ref", "HEAD")
            await update_node(tree.root_node_id, git_branch=branch, git_commit=head_sha)
            await update_tree(tree.id, repo_mode=repo_mode, repo_source=repo_source)
            updated_tree = await get_tree(tree.id)
            root_node = await get_node(tree.root_node_id)
            await send(WS.TREE_UPDATED, tree=updated_tree.model_dump())
            await send(WS.NODE_DATA, node=root_node.model_dump())
        except Exception as e:
            await send(WS.ERROR, error=f"Repo setup failed: {e}")

    async def handle_get_node_files(data: dict):
        node_id = data["node_id"]
        node = await get_node(node_id)
        if not node:
            await send(WS.ERROR, error="Node not found")
            return
        tree = await get_tree(node.tree_id)
        if not tree or tree.repo_mode == "none":
            await send(WS.ERROR, error="No repo configured")
            return
        ws_path = resolve_workspace(tree.id, tree.root_node_id, node_id, tree.repo_mode)
        if not ws_path.exists():
            await send(WS.NODE_FILES, node_id=node_id, files=[])
            return
        files = await list_files(ws_path)
        await send(WS.NODE_FILES, node_id=node_id, files=files)

    async def handle_get_node_diff(data: dict):
        node_id = data["node_id"]
        node = await get_node(node_id)
        if not node:
            await send(WS.ERROR, error="Node not found")
            return
        tree = await get_tree(node.tree_id)
        if not tree or tree.repo_mode == "none":
            await send(WS.ERROR, error="No repo configured")
            return
        ws_path = resolve_workspace(tree.id, tree.root_node_id, node_id, tree.repo_mode)
        if not ws_path.exists():
            await send(WS.NODE_DIFF, node_id=node_id, diff="")
            return
        parent_commit = None
        if node.parent_id:
            parent_node = await get_node(node.parent_id)
            if parent_node:
                parent_commit = parent_node.git_commit
        diff = await get_diff(ws_path, parent_commit)
        await send(WS.NODE_DIFF, node_id=node_id, diff=diff)

    async def handle_get_file_content(data: dict):
        node_id = data["node_id"]
        file_path = data["file_path"]
        node = await get_node(node_id)
        if not node:
            await send(WS.ERROR, error="Node not found")
            return
        tree = await get_tree(node.tree_id)
        if not tree or tree.repo_mode == "none":
            await send(WS.ERROR, error="No repo configured")
            return
        ws_path = resolve_workspace(tree.id, tree.root_node_id, node_id, tree.repo_mode)
        try:
            content = read_file(ws_path, file_path)
            await send(WS.FILE_CONTENT, node_id=node_id, file_path=file_path, content=content)
        except Exception as e:
            await send(WS.ERROR, error=f"Cannot read file: {e}")

    # ── Dispatch table ───────────────────────────────────────────────

    dispatch = {
        WS.LIST_TREES: lambda d: handle_list_trees(),
        WS.CREATE_TREE: handle_create_tree,
        WS.LOAD_TREE: handle_load_tree,
        WS.DELETE_TREE: handle_delete_tree,
        WS.BRANCH: handle_branch,
        WS.CHAT: handle_chat,
        WS.GET_NODE: handle_get_node,
        WS.SET_REPO: handle_set_repo,
        WS.GET_NODE_FILES: handle_get_node_files,
        WS.GET_NODE_DIFF: handle_get_node_diff,
        WS.GET_FILE_CONTENT: handle_get_file_content,
    }

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")
            handler = dispatch.get(msg_type)
            if handler:
                await handler(data)

    except WebSocketDisconnect:
        for task in tasks.values():
            task.cancel()


# Serve frontend
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = FRONTEND_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
