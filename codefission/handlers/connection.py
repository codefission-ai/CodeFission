"""ConnectionHandler — per-connection state and WebSocket dispatch.

Inherits handler methods from mixin classes defined in sibling modules.
"""

import asyncio
from pathlib import Path
from fastapi import WebSocket

from config import set_project_path
from events import WS
from services.trees import get_tree
from services.orchestrator import Orchestrator, StreamState

from handlers.tree_handlers import TreeHandlersMixin
from handlers.node_handlers import NodeHandlersMixin
from handlers.chat_handlers import ChatHandlersMixin
from handlers.file_handlers import FileHandlersMixin
from handlers.settings_handlers import SettingsHandlersMixin
from handlers.repo_handlers import RepoHandlersMixin
from handlers.process_handlers import ProcessHandlersMixin


# Global registry of active streams — survives WebSocket reconnects.
# Keyed by node_id, holds the StreamState with accumulated text and
# a reference to the current handler's send method.
_active_streams: dict[str, StreamState] = {}


class ConnectionHandler(
    TreeHandlersMixin,
    NodeHandlersMixin,
    ChatHandlersMixin,
    FileHandlersMixin,
    SettingsHandlersMixin,
    RepoHandlersMixin,
    ProcessHandlersMixin,
):
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

    # -- Dispatch table (class-level) ------------------------------------------

    _dispatch_table: dict = {
        WS.LIST_TREES: TreeHandlersMixin.handle_list_trees,
        WS.CREATE_TREE: TreeHandlersMixin.handle_create_tree,
        WS.LOAD_TREE: TreeHandlersMixin.handle_load_tree,
        WS.DELETE_TREE: TreeHandlersMixin.handle_delete_tree,
        WS.BRANCH: NodeHandlersMixin.handle_branch,
        WS.CHAT: ChatHandlersMixin.handle_chat,
        WS.CANCEL: ChatHandlersMixin.handle_cancel,
        WS.DUPLICATE: ChatHandlersMixin.handle_duplicate,
        WS.SELECT_TREE: SettingsHandlersMixin.handle_select_tree,
        WS.SET_EXPANDED: SettingsHandlersMixin.handle_set_expanded,
        WS.SET_SUBTREE_COLLAPSED: SettingsHandlersMixin.handle_set_subtree_collapsed,
        WS.GET_SETTINGS: SettingsHandlersMixin.handle_get_settings,
        WS.UPDATE_GLOBAL_SETTINGS: SettingsHandlersMixin.handle_update_global_settings,
        WS.UPDATE_TREE_SETTINGS: SettingsHandlersMixin.handle_update_tree_settings,
        WS.GET_NODE: NodeHandlersMixin.handle_get_node,
        WS.GET_NODE_FILES: FileHandlersMixin.handle_get_node_files,
        WS.GET_NODE_DIFF: FileHandlersMixin.handle_get_node_diff,
        WS.GET_FILE_CONTENT: FileHandlersMixin.handle_get_file_content,
        WS.GET_NODE_PROCESSES: ProcessHandlersMixin.handle_get_node_processes,
        WS.KILL_PROCESS: ProcessHandlersMixin.handle_kill_process,
        WS.KILL_ALL_PROCESSES: ProcessHandlersMixin.handle_kill_all_processes,
        WS.DELETE_NODE: NodeHandlersMixin.handle_delete_node,
        WS.GET_REPO_INFO: RepoHandlersMixin.handle_get_repo_info,
        WS.LIST_BRANCHES: RepoHandlersMixin.handle_list_branches,
        WS.MERGE_TO_BRANCH: RepoHandlersMixin.handle_merge_to_branch,
        WS.OPEN_REPO: TreeHandlersMixin.handle_open_repo,
        WS.UPDATE_BASE: RepoHandlersMixin.handle_update_base,
    }
