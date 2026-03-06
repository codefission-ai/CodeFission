"""WebSocket message handlers — thin transport layer delegating to Orchestrator."""

import asyncio
import json
import logging
from dataclasses import dataclass

from fastapi import WebSocket

from events import bus, WS, STREAM_START, STREAM_DELTA, STREAM_END, STREAM_ERROR
from providers import list_providers
from services.tree_service import (
    list_trees, get_tree, get_all_nodes, get_node,
    delete_tree, update_tree,
    get_setting, set_setting, get_global_defaults,
)
from services.chat_service import stream_chat, TextDelta, ToolStart, ToolEnd, SessionInit
from services.workspace_service import (
    resolve_workspace, cleanup_tree_workspace,
    list_files, get_diff, read_file,
)
from services.process_service import list_processes, list_tree_processes, kill_process, kill_all_in_workspace, kill_process_tree
from services.orchestrator import Orchestrator
from services.sandbox import set_sandbox, clear_sandbox, default_writable_paths

log = logging.getLogger(__name__)


@dataclass
class StreamState:
    node_id: str
    text: str = ""
    status: str = "active"   # active | done | error


class ConnectionHandler:
    """Holds per-connection state and dispatches WebSocket messages to handlers."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.orch = Orchestrator()
        self.tasks: dict[str, asyncio.Task] = {}
        self.stream_tasks: dict[str, asyncio.Task] = {}
        self.cancelled: set[str] = set()
        self.streams: dict[str, StreamState] = {}
        self.sdk_pids: dict[str, int] = {}  # node_id -> SDK subprocess PID

    async def send(self, msg_type: str, **payload):
        try:
            await self.ws.send_json({"type": msg_type, **payload})
        except Exception:
            # Socket dead — don't crash the streaming pipeline.
            # _run_chat will keep accumulating text and save to DB on completion.
            pass

    def cleanup(self):
        # Let all tasks finish — send() swallows errors on dead sockets,
        # so _run_chat will keep going and save the response to DB.
        # The SDK has max_turns, so tasks won't run forever.
        pass

    async def dispatch(self, data: dict):
        msg_type = data.get("type")
        handler = self._dispatch_table.get(msg_type)
        if handler:
            await handler(self, data)

    # ── Tree CRUD ─────────────────────────────────────────────────────

    async def handle_list_trees(self, data: dict):
        trees = await list_trees()
        last_tree_id = await get_setting("last_tree_id")
        raw = await get_setting("expanded_nodes")
        expanded_nodes = json.loads(raw) if raw else {}
        raw_cs = await get_setting("collapsed_subtrees")
        collapsed_subtrees = json.loads(raw_cs) if raw_cs else {}
        defaults = await get_global_defaults()
        await self.send(WS.TREES, trees=[t.model_dump() for t in trees],
                        last_tree_id=last_tree_id, expanded_nodes=expanded_nodes,
                        collapsed_subtrees=collapsed_subtrees,
                        global_defaults=defaults, providers=list_providers())

    async def handle_create_tree(self, data: dict):
        name = data.get("name", "Untitled")
        tree, root = await self.orch.create_tree(name, repo_mode="new")
        await self.send(WS.TREE_CREATED, tree=tree.model_dump(), root=root.model_dump())

    async def handle_load_tree(self, data: dict):
        tree_id = data["tree_id"]
        tree = await get_tree(tree_id)
        nodes = await get_all_nodes(tree_id)

        # Scan for running processes across all node workspaces
        node_processes = {}
        if tree:
            from services.workspace_service import WORKSPACES_DIR
            tree_ws = WORKSPACES_DIR / tree_id
            if tree_ws.exists():
                raw = list_tree_processes(tree_ws)
                for nid, procs in raw.items():
                    node_processes[nid] = [
                        {"pid": p.pid, "command": p.command, "ports": p.ports}
                        for p in procs
                    ]

        await self.send(
            WS.TREE_LOADED,
            tree=tree.model_dump() if tree else None,
            nodes=[n.model_dump() for n in nodes],
            node_processes=node_processes,
        )

    async def handle_delete_tree(self, data: dict):
        tree_id = data["tree_id"]
        last = await get_setting("last_tree_id")
        if last == tree_id:
            await set_setting("last_tree_id", None)
        cleanup_tree_workspace(tree_id)
        await delete_tree(tree_id)
        await self.send(WS.TREE_DELETED, tree_id=tree_id)

    # ── Node operations ───────────────────────────────────────────────

    async def handle_branch(self, data: dict):
        parent_id = data["parent_id"]
        label = data.get("label", "")
        node = await self.orch.branch(parent_id, label)
        await self.send(WS.NODE_CREATED, node=node.model_dump())

    async def handle_get_node(self, data: dict):
        node_id = data["node_id"]
        node = await get_node(node_id)
        if node:
            await self.send(WS.NODE_DATA, node=node.model_dump())

    # ── Chat streaming ────────────────────────────────────────────────

    async def handle_chat(self, data: dict):
        node_id = data["node_id"]
        content = data["content"]
        after_id = data.get("after_id")

        task = asyncio.create_task(self._run_chat(node_id, content, after_id))
        self.tasks[node_id] = task

    async def _run_chat(self, node_id: str, msg: str, after_id: str | None = None):
        nid = node_id
        try:
            # Prepare chat: create child node, resolve workspace/session/settings
            ctx = await self.orch.prepare_chat(node_id, msg, after_id=after_id)
            nid = ctx.node_id

            # Sandbox: optionally restrict subprocess writes to this tree's workspace
            if ctx.sandbox:
                set_sandbox(default_writable_paths(str(ctx.workspace.parent)))

            # Notify client of new node
            created_payload = {"node": ctx.node.model_dump()}
            if ctx.after_id:
                created_payload["after_id"] = ctx.after_id
            await self.send(WS.NODE_CREATED, **created_payload)

            # Re-key task under child id so cancel can find it
            self.tasks[nid] = self.tasks.pop(node_id, asyncio.current_task())

            # Send updated node data (now has user_message, status=active)
            await self.send(WS.NODE_DATA, node=ctx.node.model_dump())

            # Init streaming state
            self.streams[nid] = StreamState(node_id=nid)
            await bus.emit(STREAM_START, node_id=nid)
            await self.send(WS.STATUS, node_id=nid, status="active")

            # Track tool names for pairing start->end
            tool_names: dict[str, str] = {}

            # Run streaming in a separate task so the SDK's anyio
            # cancel scope is bound to that task, not ours.
            event_queue: asyncio.Queue = asyncio.Queue()
            gen = stream_chat(
                nid, ctx.sdk_message, ctx.workspace, ctx.parent_session_id,
                model=ctx.model,
                max_turns=ctx.max_turns,
                auth_mode=ctx.auth_mode,
                api_key=ctx.api_key,
            )

            async def _pump_stream():
                try:
                    async for event in gen:
                        await event_queue.put(event)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    await event_queue.put(exc)
                finally:
                    try:
                        await gen.aclose()
                    except Exception:
                        pass
                    await event_queue.put(None)
                    self.stream_tasks.pop(nid, None)

            stream_task = asyncio.create_task(_pump_stream())
            self.stream_tasks[nid] = stream_task

            # Find SDK subprocess PID (direct child of our process with cwd=workspace)
            await asyncio.sleep(0.3)  # Give subprocess time to start
            self._track_sdk_pid(nid, ctx.workspace)

            # Consume events from the queue
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    if nid in self.cancelled:
                        break
                    continue
                if event is None:
                    break
                if isinstance(event, Exception):
                    raise event

                if isinstance(event, SessionInit):
                    from services.tree_service import update_node
                    await update_node(nid, session_id=event.session_id)

                elif isinstance(event, TextDelta):
                    self.streams[nid].text += event.text
                    await bus.emit(STREAM_DELTA, node_id=nid, text=event.text)
                    await self.send(WS.CHUNK, node_id=nid, text=event.text)

                elif isinstance(event, ToolStart):
                    if event.name:
                        tool_names[event.tool_call_id] = event.name
                    await self.send(WS.TOOL_START,
                        node_id=nid,
                        tool_call_id=event.tool_call_id,
                        name=event.name,
                        arguments=event.arguments,
                    )

                elif isinstance(event, ToolEnd):
                    name = event.name or tool_names.get(event.tool_call_id, "")
                    await self.send(WS.TOOL_END,
                        node_id=nid,
                        tool_call_id=event.tool_call_id,
                        name=name,
                        result=event.result,
                        is_error=event.is_error,
                    )

            # Wait for stream task cleanup
            try:
                await stream_task
            except BaseException:
                pass

            # Check if this node was cancelled
            was_cancelled = nid in self.cancelled
            self.cancelled.discard(nid)

            if was_cancelled:
                if nid in self.streams:
                    self.streams[nid].status = "error"
                partial = self.streams.get(nid, StreamState(nid)).text
                active_tools = list(tool_names.values())
                result = await self.orch.cancel_chat(nid, partial, active_tools)
                await self.send(WS.CHUNK, node_id=nid, text=result.saved_text)
                await self.send(WS.ERROR, node_id=nid, error="Cancelled")
            else:
                full_response = self.streams[nid].text
                self.streams[nid].status = "done"
                result = await self.orch.complete_chat(nid, full_response, msg, ctx.workspace)
                await bus.emit(STREAM_END, node_id=nid, full_response=full_response)
                done_payload = {"node_id": nid, "full_response": full_response}
                if result.git_commit:
                    done_payload["git_commit"] = result.git_commit
                # Brief delay to let SDK/tool subprocesses fully exit
                await asyncio.sleep(0.15)
                # Scan for orphaned processes in the workspace
                procs = list_processes(ctx.workspace)
                if procs:
                    done_payload["processes"] = [
                        {"pid": p.pid, "command": p.command, "ports": p.ports}
                        for p in procs
                    ]
                await self.send(WS.DONE, **done_payload)

        except Exception as e:
            import traceback
            traceback.print_exc()
            if nid in self.streams:
                self.streams[nid].status = "error"
            await self.orch.fail_chat(nid)
            await bus.emit(STREAM_ERROR, node_id=nid, error=str(e))
            await self.send(WS.ERROR, node_id=nid, error=str(e))

        finally:
            clear_sandbox()
            self.streams.pop(nid, None)
            self.tasks.pop(nid, None)
            self.sdk_pids.pop(nid, None)

    def _track_sdk_pid(self, node_id: str, workspace):
        """Find the SDK subprocess PID by scanning /proc for direct children with matching cwd."""
        import os
        from pathlib import Path
        server_pid = os.getpid()
        workspace_str = str(Path(workspace).resolve())
        proc = Path("/proc")
        if not proc.exists():
            return
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat_text = (entry / "stat").read_text()
                ppid = int(stat_text.split(")")[1].split()[1])
                if ppid != server_pid:
                    continue
                cwd = str((entry / "cwd").resolve())
                if cwd.startswith(workspace_str):
                    self.sdk_pids[node_id] = int(entry.name)
                    return
            except (PermissionError, FileNotFoundError, ProcessLookupError,
                    OSError, IndexError, ValueError):
                continue

    async def handle_cancel(self, data: dict):
        node_id = data["node_id"]
        self.cancelled.add(node_id)
        # Kill the SDK subprocess tree to unstick any hanging tool (curl, etc.)
        sdk_pid = self.sdk_pids.get(node_id)
        if sdk_pid:
            kill_process_tree(sdk_pid)
            self.sdk_pids.pop(node_id, None)
        st = self.stream_tasks.get(node_id)
        if st and not st.done():
            st.cancel()

    async def handle_duplicate(self, data: dict):
        """Re-run the same user message from the same parent, creating a sibling."""
        node_id = data["node_id"]
        node = await get_node(node_id)
        if not node or not node.user_message or not node.parent_id:
            await self.send(WS.ERROR, error="Cannot duplicate this node")
            return
        await self.handle_chat({
            "node_id": node.parent_id,
            "content": node.user_message,
            "after_id": node_id,
        })

    # ── Settings & state ──────────────────────────────────────────────

    async def handle_select_tree(self, data: dict):
        tree_id = data.get("tree_id")
        await set_setting("last_tree_id", tree_id)

    async def handle_set_expanded(self, data: dict):
        node_id = data["node_id"]
        expanded = data["expanded"]
        raw = await get_setting("expanded_nodes")
        nodes_map = json.loads(raw) if raw else {}
        if expanded:
            nodes_map[node_id] = True
        else:
            nodes_map.pop(node_id, None)
        await set_setting("expanded_nodes", json.dumps(nodes_map))

    async def handle_set_subtree_collapsed(self, data: dict):
        node_id = data["node_id"]
        collapsed = data["collapsed"]
        raw = await get_setting("collapsed_subtrees")
        subtrees_map = json.loads(raw) if raw else {}
        if collapsed:
            subtrees_map[node_id] = True
        else:
            subtrees_map.pop(node_id, None)
        await set_setting("collapsed_subtrees", json.dumps(subtrees_map))

    async def handle_get_settings(self, data: dict):
        defaults = await get_global_defaults()
        await self.send(WS.SETTINGS, global_defaults=defaults, providers=list_providers())

    async def handle_update_global_settings(self, data: dict):
        for key in ("default_provider", "default_model", "default_max_turns", "auth_mode", "api_key", "sandbox"):
            if key in data:
                val = data[key]
                if key == "sandbox":
                    await set_setting(key, "true" if val else None)
                else:
                    await set_setting(key, str(val) if val is not None and val != "" else None)
        defaults = await get_global_defaults()
        await self.send(WS.SETTINGS, global_defaults=defaults, providers=list_providers())

    async def handle_update_tree_settings(self, data: dict):
        tree_id = data["tree_id"]
        updates = {}
        if "provider" in data:
            updates["provider"] = data["provider"] or ""
        if "model" in data:
            updates["model"] = data["model"] or ""
        if "max_turns" in data:
            updates["max_turns"] = data["max_turns"]
        if updates:
            await update_tree(tree_id, **updates)
        tree = await get_tree(tree_id)
        if tree:
            await self.send(WS.TREE_UPDATED, tree=tree.model_dump())

    # ── Repo & file operations ────────────────────────────────────────

    async def handle_set_repo(self, data: dict):
        tree_id = data["tree_id"]
        repo_mode = data["repo_mode"]
        repo_source = data.get("repo_source")
        try:
            updated_tree, root_node = await self.orch.set_repo(tree_id, repo_mode, repo_source)
            await self.send(WS.TREE_UPDATED, tree=updated_tree.model_dump())
            await self.send(WS.NODE_DATA, node=root_node.model_dump())
        except Exception as e:
            await self.send(WS.ERROR, error=f"Repo setup failed: {e}")

    async def handle_get_node_files(self, data: dict):
        node_id = data["node_id"]
        node = await get_node(node_id)
        if not node:
            await self.send(WS.ERROR, error="Node not found")
            return
        tree = await get_tree(node.tree_id)
        if not tree:
            await self.send(WS.ERROR, error="Tree not found")
            return
        ws_path = resolve_workspace(tree.id, tree.root_node_id, node_id)
        if not ws_path.exists():
            await self.send(WS.NODE_FILES, node_id=node_id, files=[])
            return
        files = await list_files(ws_path)
        await self.send(WS.NODE_FILES, node_id=node_id, files=files)

    async def handle_get_node_diff(self, data: dict):
        node_id = data["node_id"]
        node = await get_node(node_id)
        if not node:
            await self.send(WS.ERROR, error="Node not found")
            return
        tree = await get_tree(node.tree_id)
        if not tree:
            await self.send(WS.ERROR, error="Tree not found")
            return
        ws_path = resolve_workspace(tree.id, tree.root_node_id, node_id)
        if not ws_path.exists():
            await self.send(WS.NODE_DIFF, node_id=node_id, diff="")
            return
        parent_commit = None
        if node.parent_id:
            parent_node = await get_node(node.parent_id)
            if parent_node:
                parent_commit = parent_node.git_commit
        diff = await get_diff(ws_path, parent_commit)
        await self.send(WS.NODE_DIFF, node_id=node_id, diff=diff)

    async def handle_get_file_content(self, data: dict):
        node_id = data["node_id"]
        file_path = data["file_path"]
        node = await get_node(node_id)
        if not node:
            await self.send(WS.ERROR, error="Node not found")
            return
        tree = await get_tree(node.tree_id)
        if not tree:
            await self.send(WS.ERROR, error="Tree not found")
            return
        ws_path = resolve_workspace(tree.id, tree.root_node_id, node_id)
        try:
            content = read_file(ws_path, file_path)
            await self.send(WS.FILE_CONTENT, node_id=node_id, file_path=file_path, content=content)
        except Exception as e:
            await self.send(WS.ERROR, error=f"Cannot read file: {e}")

    # ── Process management ─────────────────────────────────────────────

    async def _resolve_node_workspace(self, node_id: str) -> tuple:
        """Resolve workspace path for a node. Returns (node, tree, workspace) or sends error."""
        node = await get_node(node_id)
        if not node:
            await self.send(WS.ERROR, error="Node not found")
            return None, None, None
        tree = await get_tree(node.tree_id)
        if not tree:
            await self.send(WS.ERROR, error="Tree not found")
            return None, None, None
        ws_path = resolve_workspace(tree.id, tree.root_node_id, node_id)
        return node, tree, ws_path

    async def handle_get_node_processes(self, data: dict):
        node_id = data["node_id"]
        _, _, ws_path = await self._resolve_node_workspace(node_id)
        if not ws_path:
            return
        procs = list_processes(ws_path)
        await self.send(WS.NODE_PROCESSES, node_id=node_id, processes=[
            {"pid": p.pid, "command": p.command, "ports": p.ports}
            for p in procs
        ])

    async def handle_kill_process(self, data: dict):
        node_id = data["node_id"]
        pid = data["pid"]
        _, _, ws_path = await self._resolve_node_workspace(node_id)
        if not ws_path:
            return
        kill_process(pid, ws_path)
        # Send back updated process list
        procs = list_processes(ws_path)
        await self.send(WS.NODE_PROCESSES, node_id=node_id, processes=[
            {"pid": p.pid, "command": p.command, "ports": p.ports}
            for p in procs
        ])

    async def handle_kill_all_processes(self, data: dict):
        node_id = data["node_id"]
        _, _, ws_path = await self._resolve_node_workspace(node_id)
        if not ws_path:
            return
        kill_all_in_workspace(ws_path)
        # Send back updated (now empty) process list
        procs = list_processes(ws_path)
        await self.send(WS.NODE_PROCESSES, node_id=node_id, processes=[
            {"pid": p.pid, "command": p.command, "ports": p.ports}
            for p in procs
        ])

    # ── Dispatch table (class-level) ──────────────────────────────────

    _dispatch_table: dict = {
        WS.LIST_TREES: handle_list_trees,
        WS.CREATE_TREE: handle_create_tree,
        WS.LOAD_TREE: handle_load_tree,
        WS.DELETE_TREE: handle_delete_tree,
        WS.BRANCH: handle_branch,
        WS.CHAT: handle_chat,
        WS.CANCEL: handle_cancel,
        WS.DUPLICATE: handle_duplicate,
        WS.SELECT_TREE: handle_select_tree,
        WS.SET_EXPANDED: handle_set_expanded,
        WS.SET_SUBTREE_COLLAPSED: handle_set_subtree_collapsed,
        WS.GET_SETTINGS: handle_get_settings,
        WS.UPDATE_GLOBAL_SETTINGS: handle_update_global_settings,
        WS.UPDATE_TREE_SETTINGS: handle_update_tree_settings,
        WS.GET_NODE: handle_get_node,
        WS.SET_REPO: handle_set_repo,
        WS.GET_NODE_FILES: handle_get_node_files,
        WS.GET_NODE_DIFF: handle_get_node_diff,
        WS.GET_FILE_CONTENT: handle_get_file_content,
        WS.GET_NODE_PROCESSES: handle_get_node_processes,
        WS.KILL_PROCESS: handle_kill_process,
        WS.KILL_ALL_PROCESSES: handle_kill_all_processes,
    }
