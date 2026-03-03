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
from tree_service import (
    create_tree, list_trees, get_tree, get_all_nodes, get_node,
    create_child_node, update_node, delete_tree,
)
from chat_service import stream_chat

app = FastAPI(title="Clawtree")

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
        tree, root = await create_tree(name, provider=provider, model=model)
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
        await delete_tree(tree_id)
        await send(WS.TREE_DELETED, tree_id=tree_id)

    async def handle_branch(data: dict):
        parent_id = data["parent_id"]
        label = data.get("label", "")
        node = await create_child_node(parent_id, label)
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

                # If this node already has a completed turn, auto-create a child
                if node.assistant_response:
                    child = await create_child_node(nid, label=msg[:40])
                    await send(WS.NODE_CREATED, node=child.model_dump())
                    nid = child.id

                # Save user message and set label
                current = await get_node(nid)
                label = current.label if current and current.label and current.label != "" else msg[:40]
                await update_node(nid, user_message=msg, label=label, status="active")
                await send(WS.NODE_DATA, node=(await get_node(nid)).model_dump())

                # Init streaming state
                _streams[nid] = StreamState(node_id=nid)
                await bus.emit(STREAM_START, node_id=nid)
                await send(WS.STATUS, node_id=nid, status="active")

                # Stream response
                async for chunk in stream_chat(nid, msg):
                    _streams[nid].text += chunk
                    await bus.emit(STREAM_DELTA, node_id=nid, text=chunk)
                    await send(WS.CHUNK, node_id=nid, text=chunk)

                # Finalise
                full_response = _streams[nid].text
                _streams[nid].status = "done"
                await update_node(nid, assistant_response=full_response, status="done")
                await bus.emit(STREAM_END, node_id=nid, full_response=full_response)
                await send(WS.DONE, node_id=nid, full_response=full_response)

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

    # ── Dispatch table ───────────────────────────────────────────────

    dispatch = {
        WS.LIST_TREES: lambda d: handle_list_trees(),
        WS.CREATE_TREE: handle_create_tree,
        WS.LOAD_TREE: handle_load_tree,
        WS.DELETE_TREE: handle_delete_tree,
        WS.BRANCH: handle_branch,
        WS.CHAT: handle_chat,
        WS.GET_NODE: handle_get_node,
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
