"""WebSocket message handlers — extracted from main.py to reduce monolith size."""

import asyncio
import json
import logging
from dataclasses import dataclass, field

from fastapi import WebSocket

from events import bus, WS, STREAM_START, STREAM_DELTA, STREAM_END, STREAM_ERROR
from providers import list_providers
from services.tree_service import (
    create_tree, list_trees, get_tree, get_all_nodes, get_node,
    create_child_node, update_node, update_tree, delete_tree,
    get_setting, set_setting, get_global_defaults, resolve_tree_settings,
)
from services.chat_service import stream_chat, TextDelta, ToolStart, ToolEnd, SessionInit
from services.workspace_service import (
    setup_repo, create_worktree, ensure_worktree, auto_commit,
    resolve_workspace, cleanup_tree_workspace, copy_session,
    list_files, get_diff, read_file, _run_git, WORKSPACES_DIR,
)

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
        self.tasks: dict[str, asyncio.Task] = {}
        self.stream_tasks: dict[str, asyncio.Task] = {}
        self.cancelled: set[str] = set()
        self.streams: dict[str, StreamState] = {}

    async def send(self, msg_type: str, **payload):
        await self.ws.send_json({"type": msg_type, **payload})

    def cleanup(self):
        for task in self.stream_tasks.values():
            task.cancel()
        for task in self.tasks.values():
            task.cancel()

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
        defaults = await get_global_defaults()
        await self.send(WS.TREES, trees=[t.model_dump() for t in trees],
                        last_tree_id=last_tree_id, expanded_nodes=expanded_nodes,
                        global_defaults=defaults, providers=list_providers())

    async def handle_create_tree(self, data: dict):
        name = data.get("name", "Untitled")
        tree, root = await create_tree(name, repo_mode="new")
        await setup_repo(tree.id, root.id, "new", None)
        root_dir = WORKSPACES_DIR / tree.id / root.id
        _, head_sha, _ = await _run_git(root_dir, "rev-parse", "HEAD")
        _, branch, _ = await _run_git(root_dir, "rev-parse", "--abbrev-ref", "HEAD")
        await update_node(root.id, git_branch=branch, git_commit=head_sha)
        root = await get_node(root.id)
        await self.send(WS.TREE_CREATED, tree=tree.model_dump(), root=root.model_dump())

    async def handle_load_tree(self, data: dict):
        tree_id = data["tree_id"]
        tree = await get_tree(tree_id)
        nodes = await get_all_nodes(tree_id)
        await self.send(
            WS.TREE_LOADED,
            tree=tree.model_dump() if tree else None,
            nodes=[n.model_dump() for n in nodes],
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
        node = await create_child_node(parent_id, label)

        parent = await get_node(parent_id)
        if parent:
            tree = await get_tree(parent.tree_id)
            if tree:
                try:
                    await create_worktree(
                        tree.id, tree.root_node_id, node.id,
                        parent.git_commit or "HEAD",
                    )
                    branch_name = f"ct-{node.id}"
                    await update_node(node.id, git_branch=branch_name)
                    node = await get_node(node.id)
                except Exception as e:
                    log.warning("Worktree creation failed: %s", e)

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
            node = await get_node(nid)
            if not node:
                return

            tree = await get_tree(node.tree_id)
            if not tree:
                return

            # Always create a child node (root stays as a clean hub)
            child = await create_child_node(nid, label=msg[:40])
            created_payload = {"node": child.model_dump()}
            if after_id:
                created_payload["after_id"] = after_id
            await self.send(WS.NODE_CREATED, **created_payload)
            nid = child.id
            # Re-key task under child id so cancel can find it
            self.tasks[nid] = self.tasks.pop(node_id, asyncio.current_task())

            # Save user message and set label
            current = await get_node(nid)
            label = current.label if current and current.label and current.label != "" else msg[:40]
            await update_node(nid, user_message=msg, label=label, status="active")
            await self.send(WS.NODE_DATA, node=(await get_node(nid)).model_dump())

            # Resolve workspace and ensure worktree exists
            workspace = resolve_workspace(tree.id, tree.root_node_id, nid)
            current = await get_node(nid)
            parent_node = await get_node(current.parent_id) if current.parent_id else None
            await ensure_worktree(
                tree.id, tree.root_node_id, nid,
                current.parent_id,
                parent_node.git_commit if parent_node else None,
            )

            # Resolve parent's session_id for forking
            current = await get_node(nid)
            parent_session_id = None
            sdk_msg = msg
            if current and current.parent_id:
                parent_node = await get_node(current.parent_id)
                if parent_node and parent_node.session_id:
                    parent_session_id = parent_node.session_id
                    parent_ws = resolve_workspace(tree.id, tree.root_node_id, parent_node.id)
                    copy_session(parent_ws, workspace, parent_session_id)
                # If parent was cancelled, include full context
                if parent_node and parent_node.status == "error" and "[Cancelled by user" in (parent_node.assistant_response or ""):
                    partial = parent_node.assistant_response or ""
                    sdk_msg = (
                        "[System: Your previous response was cancelled by the user. "
                        "The session was interrupted mid-execution. Here is your "
                        "partial response up to the point of cancellation:\n\n"
                        f"{partial}\n\n"
                        "Resume from this context. The user's new message follows.]\n\n"
                        + msg
                    )

            # Resolve effective settings (tree overrides + global defaults)
            effective = await resolve_tree_settings(tree)
            global_cfg = await get_global_defaults()

            # Init streaming state
            self.streams[nid] = StreamState(node_id=nid)
            await bus.emit(STREAM_START, node_id=nid)
            await self.send(WS.STATUS, node_id=nid, status="active")

            # Track tool names for pairing start->end
            tool_names: dict[str, str] = {}
            node_session_id: str | None = None

            # Run streaming in a separate task so the SDK's anyio
            # cancel scope is bound to that task, not ours.
            event_queue: asyncio.Queue = asyncio.Queue()
            gen = stream_chat(
                nid, sdk_msg, workspace, parent_session_id,
                model=effective["model"],
                max_turns=effective["max_turns"],
                auth_mode=global_cfg["auth_mode"],
                api_key=global_cfg["api_key"],
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

            # Consume events from the queue
            while True:
                event = await event_queue.get()
                if event is None:
                    break
                if isinstance(event, Exception):
                    raise event

                if isinstance(event, SessionInit):
                    node_session_id = event.session_id
                    await update_node(nid, session_id=node_session_id)

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
                active_tools = [name for name in tool_names.values()]
                cancel_note = "\n\n---\n*[Cancelled by user]*"
                if active_tools:
                    cancel_note = (
                        "\n\n---\n*[Cancelled by user while running: "
                        + ", ".join(active_tools) + "]*"
                    )
                full = partial + cancel_note
                await update_node(nid, status="error", assistant_response=full)
                await self.send(WS.CHUNK, node_id=nid, text=cancel_note)
                await self.send(WS.ERROR, node_id=nid, error="Cancelled")
            else:
                full_response = self.streams[nid].text
                self.streams[nid].status = "done"
                await update_node(nid, assistant_response=full_response, status="done")

                git_commit = None
                try:
                    commit_sha, files_changed = await auto_commit(workspace, msg)
                    await update_node(nid, git_commit=commit_sha)
                    git_commit = commit_sha
                except Exception as e:
                    log.warning("Auto-commit failed: %s", e)

                await bus.emit(STREAM_END, node_id=nid, full_response=full_response)
                done_payload = {"node_id": nid, "full_response": full_response}
                if git_commit:
                    done_payload["git_commit"] = git_commit
                await self.send(WS.DONE, **done_payload)

        except Exception as e:
            import traceback
            traceback.print_exc()
            if nid in self.streams:
                self.streams[nid].status = "error"
            await update_node(nid, status="error")
            await bus.emit(STREAM_ERROR, node_id=nid, error=str(e))
            await self.send(WS.ERROR, node_id=nid, error=str(e))

        finally:
            self.streams.pop(nid, None)
            self.tasks.pop(nid, None)

    async def handle_cancel(self, data: dict):
        node_id = data["node_id"]
        self.cancelled.add(node_id)
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

    async def handle_get_settings(self, data: dict):
        defaults = await get_global_defaults()
        await self.send(WS.SETTINGS, global_defaults=defaults, providers=list_providers())

    async def handle_update_global_settings(self, data: dict):
        for key in ("default_provider", "default_model", "default_max_turns", "auth_mode", "api_key"):
            if key in data:
                val = data[key]
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
        tree = await get_tree(tree_id)
        if not tree or not tree.root_node_id:
            await self.send(WS.ERROR, error="Tree not found")
            return
        try:
            root_dir = WORKSPACES_DIR / tree.id / tree.root_node_id
            if root_dir.exists() and repo_mode != tree.repo_mode:
                import shutil
                shutil.rmtree(root_dir, ignore_errors=True)
            await setup_repo(tree.id, tree.root_node_id, repo_mode, repo_source)
            root_dir = WORKSPACES_DIR / tree.id / tree.root_node_id
            _, head_sha, _ = await _run_git(root_dir, "rev-parse", "HEAD")
            _, branch, _ = await _run_git(root_dir, "rev-parse", "--abbrev-ref", "HEAD")
            await update_node(tree.root_node_id, git_branch=branch, git_commit=head_sha)
            await update_tree(tree.id, repo_mode=repo_mode, repo_source=repo_source)
            updated_tree = await get_tree(tree.id)
            root_node = await get_node(tree.root_node_id)
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
        WS.GET_SETTINGS: handle_get_settings,
        WS.UPDATE_GLOBAL_SETTINGS: handle_update_global_settings,
        WS.UPDATE_TREE_SETTINGS: handle_update_tree_settings,
        WS.GET_NODE: handle_get_node,
        WS.SET_REPO: handle_set_repo,
        WS.GET_NODE_FILES: handle_get_node_files,
        WS.GET_NODE_DIFF: handle_get_node_diff,
        WS.GET_FILE_CONTENT: handle_get_file_content,
    }
