"""WebSocket message handlers — thin transport layer delegating to Orchestrator.

This is the WS Presenter in the Model-View-Presenter pattern.
It contains zero business logic — only WS message parsing, Orchestrator
calls, and WS response formatting.
"""

import asyncio
import json
import logging
from pathlib import Path
from fastapi import WebSocket

from config import set_project_path, get_project_path
from events import bus, WS, STREAM_START, STREAM_DELTA, STREAM_END, STREAM_ERROR
from providers import list_providers
from services.trees import (
    list_trees, get_tree, get_all_nodes, get_node, find_tree,
    delete_tree, update_tree, update_node,
    get_setting, set_setting, get_global_defaults,
)
from services.chat import TextDelta, ToolStart, ToolEnd, SessionInit
from services.workspace import (
    resolve_workspace,
    remove_worktree, remove_worktree_and_branch,
    list_branches as ws_list_branches,
    get_repo_info as ws_get_repo_info,
    check_staleness,
    _worktrees_dir,
    detect_repo_name,
)
from services.process_service import list_processes, list_tree_processes, kill_process, kill_all_in_workspace, kill_process_tree
from services.orchestrator import Orchestrator, StreamState

log = logging.getLogger(__name__)


# Global registry of active streams — survives WebSocket reconnects.
# Keyed by node_id, holds the StreamState with accumulated text and
# a reference to the current handler's send method.
_active_streams: dict[str, StreamState] = {}


class ConnectionHandler:
    """Holds per-connection state and dispatches WebSocket messages to handlers."""

    def __init__(self, ws: WebSocket, repo_path: str | None = None,
                 repo_id: str | None = None, head_commit: str | None = None,
                 orchestrator: Orchestrator | None = None):
        self.ws = ws
        self.repo_path = Path(repo_path) if repo_path else None
        self.repo_id = repo_id
        self.head_commit = head_commit
        self.orch = orchestrator or Orchestrator()
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancelled: set[str] = set()
        self.streams: dict[str, StreamState] = {}

    async def send(self, msg_type: str, **payload):
        # If this stream has been claimed by a newer connection, route there
        node_id = payload.get("node_id")
        if node_id:
            info = _active_streams.get(node_id)
            if info and info.send_fn is not None and info.send_fn != self.send:
                try:
                    await info.send_fn(msg_type, **payload)
                except Exception:
                    pass
                return
        try:
            await self.ws.send_json({"type": msg_type, **payload})
        except Exception:
            pass

    def cleanup(self):
        # Detach our send function from any active streams so the old
        # (dead) socket isn't used.  Tasks keep running — send() will
        # silently no-op until a new connection claims the stream.
        for info in _active_streams.values():
            if info.send_fn == self.send:
                info.send_fn = None

    def _set_context_for_repo(self, repo_path: Path):
        """Set project context for git operations."""
        set_project_path(repo_path)

    async def _set_context_for_tree(self, tree_id: str) -> bool:
        """Set project path from tree's repo_path before git operations.
        Returns True if context was set, False otherwise."""
        tree = await get_tree(tree_id)
        if tree and tree.repo_path:
            repo_path = Path(tree.repo_path)
            if repo_path.is_dir():
                set_project_path(repo_path)
                return True
        # Fall back to current repo_path on the connection
        if self.repo_path:
            set_project_path(self.repo_path)
            return True
        return False

    async def dispatch(self, data: dict):
        msg_type = data.get("type")
        if msg_type == "ping":
            await self.send("pong")
            return
        # Set project context for this handler call
        if self.repo_path:
            set_project_path(self.repo_path)
        handler = self._dispatch_table.get(msg_type)
        if handler:
            await handler(self, data)

    # ── Repo operations ──────────────────────────────────────────────

    async def handle_open_repo(self, data: dict):
        """Open a repo: find or create tree for the given repo_id + head_commit."""
        repo_path_str = data.get("repo_path") or (str(self.repo_path) if self.repo_path else None)
        repo_id = data.get("repo_id") or self.repo_id
        head_commit = data.get("head_commit") or self.head_commit

        if not repo_path_str or not repo_id or not head_commit:
            await self.send(WS.ERROR, error="Missing repo context")
            return

        repo_path = Path(repo_path_str)
        if not repo_path.is_dir():
            await self.send(WS.ERROR, error=f"Not a directory: {repo_path}")
            return

        # Update connection state
        self.repo_path = repo_path
        self.repo_id = repo_id
        self.head_commit = head_commit
        set_project_path(repo_path)

        repo_name = detect_repo_name(repo_path)

        # Find existing tree for this repo+commit
        tree = await find_tree(repo_id, head_commit, str(repo_path))

        if not tree:
            # Create new tree
            try:
                from services.workspace import _run_git
                _, actual_branch, _ = await _run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
                tree, root = await self.orch.create_tree(
                    repo_name, base_branch=actual_branch,
                    repo_id=repo_id, repo_path=str(repo_path), repo_name=repo_name,
                )
                from services.workspace import create_protective_ref
                if tree.base_commit:
                    await create_protective_ref(tree.id, tree.base_commit)
            except Exception as e:
                log.warning("Auto-create tree failed: %s", e)
                await self.send(WS.ERROR, error=f"Failed to create tree: {e}")
                return

        # Load tree data
        nodes = await get_all_nodes(tree.id)
        staleness = {"stale": False, "commits_behind": 0}
        if tree.base_commit:
            staleness = await check_staleness(tree.base_branch, tree.base_commit)

        info = await ws_get_repo_info(repo_path)
        branches = await ws_list_branches()

        await self.send(WS.REPO_OPENED, **info,
                        tree=tree.model_dump(),
                        nodes=[n.model_dump() for n in nodes],
                        staleness=staleness,
                        branches=branches,
                        repo_id=repo_id, repo_name=repo_name)

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
        base_branch = data.get("base_branch", "main")
        try:
            tree, root = await self.orch.create_tree(
                name, base_branch=base_branch,
                repo_id=self.repo_id,
                repo_path=str(self.repo_path) if self.repo_path else None,
                repo_name=detect_repo_name(self.repo_path) if self.repo_path else None,
            )
            await self.send(WS.TREE_CREATED, tree=tree.model_dump(), root=root.model_dump())
        except Exception as e:
            await self.send(WS.ERROR, error=f"Failed to create tree: {e}")

    async def handle_load_tree(self, data: dict):
        tree_id = data["tree_id"]
        await self._set_context_for_tree(tree_id)
        tree = await get_tree(tree_id)
        nodes = await get_all_nodes(tree_id)

        # Scan for running processes across all node workspaces
        node_processes = {}
        if tree and tree.root_node_id:
            wt_dir = _worktrees_dir()
            if wt_dir.exists():
                raw = list_tree_processes(wt_dir)
                for nid, procs in raw.items():
                    node_processes[nid] = [
                        {"pid": p.pid, "command": p.command, "ports": p.ports}
                        for p in procs
                    ]

        # Check staleness
        staleness = {"stale": False, "commits_behind": 0}
        if tree and tree.base_commit:
            staleness = await check_staleness(tree.base_branch, tree.base_commit)

        await self.send(
            WS.TREE_LOADED,
            tree=tree.model_dump() if tree else None,
            nodes=[n.model_dump() for n in nodes],
            node_processes=node_processes,
            staleness=staleness,
        )

        # Reconnect any active streams for this tree to the new connection.
        for nid, info in _active_streams.items():
            if info.tree_id == tree_id and info.status == "active":
                info.send_fn = self.send
                await self.send(WS.STATUS, node_id=nid, status="active")
                if info.text:
                    await self.send(WS.CHUNK, node_id=nid, text=info.text)

    async def handle_delete_tree(self, data: dict):
        tree_id = data["tree_id"]
        await self._set_context_for_tree(tree_id)
        last = await get_setting("last_tree_id")
        if last == tree_id:
            await set_setting("last_tree_id", None)
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
        file_quotes = data.get("file_quotes") or []
        draft_node_id = data.get("draft_node_id")

        # Set context for the tree this node belongs to
        node = await get_node(node_id)
        if node:
            await self._set_context_for_tree(node.tree_id)

        task = asyncio.create_task(self._run_chat(node_id, content, after_id, file_quotes, draft_node_id))
        self.tasks[node_id] = task

    async def _run_chat(self, node_id: str, msg: str, after_id: str | None = None, file_quotes: list[dict] | None = None, draft_node_id: str | None = None):
        """Thin WS consumer of Orchestrator.chat() async generator.

        Translates domain events into WebSocket messages. All business logic
        lives in the Orchestrator; this method is pure transport.
        """
        from services.orchestrator import ChatNodeCreated, ChatCompleted

        nid = node_id
        tool_names: dict[str, str] = {}

        try:
            async for event in self.orch.chat(
                node_id, msg,
                after_id=after_id,
                file_quotes=file_quotes,
                draft_node_id=draft_node_id,
            ):
                if isinstance(event, ChatNodeCreated):
                    nid = event.node.id
                    created_payload = {"node": event.node.model_dump()}
                    if event.after_id:
                        created_payload["after_id"] = event.after_id
                    await self.send(WS.NODE_CREATED, **created_payload)

                    # Re-key task under child id so cancel can find it
                    self.tasks[nid] = self.tasks.pop(node_id, asyncio.current_task())

                    # Send node data (now has user_message, status=active)
                    await self.send(WS.NODE_DATA, node=event.node.model_dump())

                    # Auto-name tree on first message
                    tree = await get_tree(event.node.tree_id)
                    if tree and tree.name == "Untitled":
                        asyncio.create_task(self._auto_name_tree(tree.id, msg, tree))

                    # Init streaming state for reconnect support
                    state = StreamState(node_id=nid, tree_id=event.node.tree_id, send_fn=self.send)
                    self.streams[nid] = state
                    _active_streams[nid] = state
                    await bus.emit(STREAM_START, node_id=nid)
                    await self.send(WS.STATUS, node_id=nid, status="active")

                elif isinstance(event, SessionInit):
                    pass  # Orchestrator handles session_id saving

                elif isinstance(event, TextDelta):
                    if nid in self.streams:
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

                elif isinstance(event, ChatCompleted):
                    if nid in self.streams:
                        self.streams[nid].status = "done"
                    full_response = event.result.full_response
                    await bus.emit(STREAM_END, node_id=nid, full_response=full_response)
                    done_payload = {"node_id": nid, "full_response": full_response}
                    if event.result.git_commit:
                        done_payload["git_commit"] = event.result.git_commit

                    # Brief delay to let SDK/tool subprocesses fully exit
                    await asyncio.sleep(0.15)
                    # Tree-wide process scan
                    wt_dir = _worktrees_dir()
                    tree_procs_raw = list_tree_processes(wt_dir) if wt_dir.exists() else {}
                    this_node_procs = tree_procs_raw.get(nid, [])
                    if this_node_procs:
                        done_payload["processes"] = [
                            {"pid": p.pid, "command": p.command, "ports": p.ports}
                            for p in this_node_procs
                        ]
                    else:
                        # No running processes — remove ephemeral worktree
                        try:
                            node = await get_node(nid)
                            if node:
                                tree = await get_tree(node.tree_id)
                                if tree:
                                    if event.result.files_changed == 0 and node.parent_id:
                                        await remove_worktree_and_branch(tree.root_node_id, nid)
                                        await update_node(nid, git_branch=None)
                                    else:
                                        await remove_worktree(tree.root_node_id, nid)
                        except Exception:
                            log.debug("Ephemeral worktree removal failed for %s", nid, exc_info=True)

                    await self.send(WS.DONE, **done_payload)
                    await self._send_tree_processes()

        except Exception as e:
            import traceback
            traceback.print_exc()
            if nid in self.streams:
                self.streams[nid].status = "error"
            await bus.emit(STREAM_ERROR, node_id=nid, error=str(e))
            await self.send(WS.ERROR, node_id=nid, error=str(e))
            # Try to remove ephemeral worktree on error
            try:
                node = await get_node(nid)
                if node:
                    tree = await get_tree(node.tree_id)
                    if tree:
                        ws_path = resolve_workspace(tree.root_node_id, nid)
                        procs = list_processes(ws_path) if ws_path.exists() else []
                        if not procs:
                            await remove_worktree(tree.root_node_id, nid)
                        await self._send_tree_processes()
            except Exception:
                log.debug("Ephemeral worktree removal after error failed for %s", nid, exc_info=True)

        finally:
            self.streams.pop(nid, None)
            _active_streams.pop(nid, None)
            self.tasks.pop(nid, None)

    async def _auto_name_tree(self, tree_id: str, first_message: str, tree):
        """Background task: generate a short name for a tree and push it to the client."""
        try:
            from services.summary_service import generate_tree_name

            defaults = await get_global_defaults()
            summary_model = defaults.get("summary_model") or ""
            if not summary_model:
                return  # auto-naming disabled
            api_key = defaults.get("api_key") or None

            repo_info = tree.base_branch or "main"
            auth_mode = defaults.get("auth_mode", "cli")
            name = await generate_tree_name(
                skill=tree.skill,
                repo_info=repo_info,
                first_message=first_message,
                model=summary_model,
                auth_mode=auth_mode,
                api_key=api_key,
            )
            if name:
                await update_tree(tree_id, name=name)
                updated = await get_tree(tree_id)
                if updated:
                    await self.send(WS.TREE_UPDATED, tree=updated.model_dump())
        except Exception:
            log.debug("Auto-name tree failed for %s", tree_id, exc_info=True)

    async def handle_cancel(self, data: dict):
        node_id = data["node_id"]
        # Use global registry so cancel works after reconnect
        info = _active_streams.get(node_id)
        if info:
            info.cancelled = True
            if info.sdk_pid:
                kill_process_tree(info.sdk_pid)
                info.sdk_pid = None
            if info.stream_task and not info.stream_task.done():
                info.stream_task.cancel()
        else:
            # Fallback: cancel the task directly
            self.cancelled.add(node_id)
            task = self.tasks.get(node_id)
            if task and not task.done():
                task.cancel()

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
        """Delegate to Orchestrator, format WS response."""
        defaults = await self.orch.update_global_settings(data)
        await self.send(WS.SETTINGS, global_defaults=defaults, providers=list_providers())

    async def handle_update_tree_settings(self, data: dict):
        """Delegate to Orchestrator, format WS response."""
        tree_id = data["tree_id"]
        tree = await self.orch.update_tree_settings(tree_id, data)
        if tree:
            await self.send(WS.TREE_UPDATED, tree=tree.model_dump())

    # ── Repo & branch operations ──────────────────────────────────────

    async def handle_get_repo_info(self, data: dict):
        info = await ws_get_repo_info()
        await self.send(WS.REPO_INFO, **info)

    async def handle_list_branches(self, data: dict):
        branches = await ws_list_branches()
        await self.send(WS.BRANCHES, branches=branches)

    async def handle_merge_to_branch(self, data: dict):
        node_id = data["node_id"]
        target_branch = data["target_branch"]
        # Set context for the node's tree
        node = await get_node(node_id)
        if node:
            await self._set_context_for_tree(node.tree_id)
        result = await self.orch.merge_to_branch(node_id, target_branch)
        await self.send(WS.MERGE_RESULT, node_id=node_id, **result)

    # ── File operations — delegate to Orchestrator ────────────────────

    async def handle_get_node_files(self, data: dict):
        node_id = data["node_id"]
        node = await get_node(node_id)
        if not node:
            await self.send(WS.ERROR, error="Node not found")
            return
        await self._set_context_for_tree(node.tree_id)
        try:
            result = await self.orch.list_node_files(node_id)
            await self.send(WS.NODE_FILES, node_id=result.node_id, files=result.files)
        except ValueError as e:
            await self.send(WS.ERROR, error=str(e))

    async def handle_get_node_diff(self, data: dict):
        node_id = data["node_id"]
        node = await get_node(node_id)
        if not node:
            await self.send(WS.ERROR, error="Node not found")
            return
        await self._set_context_for_tree(node.tree_id)
        try:
            result = await self.orch.get_node_diff(node_id)
            await self.send(WS.NODE_DIFF, node_id=result.node_id, diff=result.diff)
        except ValueError as e:
            await self.send(WS.ERROR, error=str(e))

    async def handle_get_file_content(self, data: dict):
        node_id = data["node_id"]
        file_path = data["file_path"]
        node = await get_node(node_id)
        if not node:
            await self.send(WS.ERROR, error="Node not found")
            return
        await self._set_context_for_tree(node.tree_id)
        try:
            result = await self.orch.read_node_file(node_id, file_path)
            await self.send(WS.FILE_CONTENT, node_id=result.node_id, file_path=result.file_path, content=result.content)
        except Exception as e:
            await self.send(WS.ERROR, error=f"Cannot read file: {e}")

    # ── Process management ─────────────────────────────────────────────

    async def _send_tree_processes(self):
        """Scan and broadcast tree-wide process state so the UI stays in sync."""
        try:
            wt_dir = _worktrees_dir()
            if not wt_dir.exists():
                return
            raw = list_tree_processes(wt_dir)
            tree_procs = {}
            for nid, procs in raw.items():
                tree_procs[nid] = [
                    {"pid": p.pid, "command": p.command, "ports": p.ports}
                    for p in procs
                ]
            await self.send("tree_node_processes", tree_node_processes=tree_procs)
        except Exception:
            log.debug("Tree-wide process scan failed", exc_info=True)

    async def _cleanup_node_worktree(self, node, tree):
        """Remove a node's worktree after all processes have exited."""
        try:
            node_id = node.id
            if not node.parent_id:
                return  # Never remove root workspace (project path)
            # Check if node has unique changes vs parent
            parent = await get_node(node.parent_id)
            if parent and node.git_commit and node.git_commit == parent.git_commit:
                await remove_worktree_and_branch(tree.root_node_id, node_id)
                await update_node(node_id, git_branch=None)
            else:
                await remove_worktree(tree.root_node_id, node_id)
        except Exception:
            log.debug("Worktree cleanup failed for %s", node.id, exc_info=True)

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
        await self._set_context_for_tree(node.tree_id)
        ws_path = resolve_workspace(tree.root_node_id, node_id)
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
        node, tree, ws_path = await self._resolve_node_workspace(node_id)
        if not ws_path:
            return
        kill_process(pid, ws_path)
        # Broadcast tree-wide process state
        await self._send_tree_processes()
        # If no more processes on this node, clean up the worktree
        procs = list_processes(ws_path)
        if not procs and tree and node:
            await self._cleanup_node_worktree(node, tree)

    async def handle_kill_all_processes(self, data: dict):
        node_id = data["node_id"]
        node, tree, ws_path = await self._resolve_node_workspace(node_id)
        if not ws_path:
            return
        kill_all_in_workspace(ws_path)
        # Broadcast tree-wide process state
        await self._send_tree_processes()
        # If no more processes on this node, clean up the worktree
        procs = list_processes(ws_path)
        if not procs and tree and node:
            await self._cleanup_node_worktree(node, tree)

    # ── Node deletion — delegate to Orchestrator ──────────────────────

    async def handle_delete_node(self, data: dict):
        node_id = data["node_id"]
        node = await get_node(node_id)
        if node:
            await self._set_context_for_tree(node.tree_id)

        # Pass active streams to orchestrator for checking
        self.orch._active_streams = _active_streams

        try:
            result = await self.orch.delete_node(node_id)
            await self.send(WS.NODES_DELETED,
                            deleted_ids=result.deleted_ids,
                            updated_nodes=[n.model_dump() for n in result.updated_nodes])
        except ValueError as e:
            await self.send(WS.ERROR, error=str(e))

    # ── Update base — delegate to Orchestrator ────────────────────────

    async def handle_update_base(self, data: dict):
        tree_id = data["tree_id"]
        new_path = data.get("repo_path")
        new_branch = data.get("base_branch")
        new_commit = data.get("base_commit")

        # Set context from existing tree if no new path
        if not new_path or new_path == (await get_tree(tree_id) or object).__dict__.get("repo_path"):
            await self._set_context_for_tree(tree_id)

        try:
            result = await self.orch.update_base(
                tree_id,
                new_path=new_path,
                new_branch=new_branch,
                new_commit=new_commit,
                repo_path_context=self.repo_path,
            )

            # Update connection state if path changed
            if new_path:
                self.repo_path = Path(new_path)

            extra = {}
            if result.branches:
                extra["branches"] = result.branches

            if result.existing_tree_id:
                await self.send(WS.BASE_UPDATED, existing_tree_id=result.existing_tree_id,
                                tree=result.tree.model_dump(), **extra)
            else:
                await self.send(WS.BASE_UPDATED, tree=result.tree.model_dump(),
                                staleness=result.staleness, **extra)

        except ValueError as e:
            await self.send(WS.ERROR, error=str(e))

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
        WS.GET_NODE_FILES: handle_get_node_files,
        WS.GET_NODE_DIFF: handle_get_node_diff,
        WS.GET_FILE_CONTENT: handle_get_file_content,
        WS.GET_NODE_PROCESSES: handle_get_node_processes,
        WS.KILL_PROCESS: handle_kill_process,
        WS.KILL_ALL_PROCESSES: handle_kill_all_processes,
        WS.DELETE_NODE: handle_delete_node,
        WS.GET_REPO_INFO: handle_get_repo_info,
        WS.LIST_BRANCHES: handle_list_branches,
        WS.MERGE_TO_BRANCH: handle_merge_to_branch,
        WS.OPEN_REPO: handle_open_repo,
        WS.UPDATE_BASE: handle_update_base,
    }
