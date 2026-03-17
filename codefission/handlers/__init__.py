"""handlers — WS transport layer.

Translates WebSocket JSON messages into orchestrator calls and formats
results back as WS responses. Each file is a mixin for ConnectionHandler,
grouped by domain: chat, trees, nodes, files, settings, repo, processes.

handlers/ only calls orchestrator/. Never touches store/ directly.
"""

import asyncio
from pathlib import Path
from fastapi import WebSocket

from config import set_project_path
from events import WS
from orchestrator import Orchestrator, StreamState

from handlers.trees import TreesMixin
from handlers.nodes import NodesMixin
from handlers.chat import ChatMixin
from handlers.files import FilesMixin
from handlers.settings import SettingsMixin, list_providers
from handlers.repo import RepoMixin
from handlers.processes import ProcessesMixin


# Global registry of active streams — survives WebSocket reconnects.
_active_streams: dict[str, StreamState] = {}


class ConnectionHandler(
    TreesMixin,
    NodesMixin,
    ChatMixin,
    FilesMixin,
    SettingsMixin,
    RepoMixin,
    ProcessesMixin,
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
        import logging
        _log = logging.getLogger("handlers.send")

        node_id = payload.get("node_id")
        if node_id:
            info = _active_streams.get(node_id)
            if info:
                if info.send_fn is None:
                    # Connection died, stream not yet reclaimed. Don't try to send
                    # on the dead WS — the text is buffered in info.text and will
                    # be sent when a new connection reclaims via LOAD_TREE.
                    return
                if info.send_fn != self.send:
                    # Stream was reclaimed by a newer connection — route there
                    try:
                        await info.send_fn(msg_type, **payload)
                    except Exception:
                        _log.debug("Rerouted send failed for %s %s", msg_type, node_id)
                    return
        try:
            await self.ws.send_json({"type": msg_type, **payload})
        except Exception as e:
            _log.debug("WS send failed (%s): %s %s", e, msg_type, node_id or "")

    def cleanup(self):
        for info in _active_streams.values():
            if info.send_fn == self.send:
                info.send_fn = None

    def _set_context_for_repo(self, repo_path: Path):
        set_project_path(repo_path)

    async def _set_context_for_tree(self, tree_id: str) -> bool:
        tree = await self.orch.get_tree(tree_id)
        if tree and tree.repo_path:
            repo_path = Path(tree.repo_path)
            if repo_path.is_dir():
                set_project_path(repo_path)
                return True
        if self.repo_path:
            set_project_path(self.repo_path)
            return True
        return False

    async def dispatch(self, data: dict):
        msg_type = data.get("type")
        if msg_type == "ping":
            await self.send("pong")
            return
        if self.repo_path:
            set_project_path(self.repo_path)
        handler = self._dispatch_table.get(msg_type)
        if handler:
            await handler(self, data)

    _dispatch_table: dict = {
        WS.LIST_TREES: TreesMixin.handle_list_trees,
        WS.CREATE_TREE: TreesMixin.handle_create_tree,
        WS.LOAD_TREE: TreesMixin.handle_load_tree,
        WS.DELETE_TREE: TreesMixin.handle_delete_tree,
        WS.BRANCH: NodesMixin.handle_branch,
        WS.CHAT: ChatMixin.handle_chat,
        WS.CANCEL: ChatMixin.handle_cancel,
        WS.DUPLICATE: ChatMixin.handle_duplicate,
        WS.SELECT_TREE: SettingsMixin.handle_select_tree,
        WS.GET_SETTINGS: SettingsMixin.handle_get_settings,
        WS.UPDATE_GLOBAL_SETTINGS: SettingsMixin.handle_update_global_settings,
        WS.UPDATE_TREE_SETTINGS: SettingsMixin.handle_update_tree_settings,
        WS.GET_NODE: NodesMixin.handle_get_node,
        WS.GET_NODE_FILES: FilesMixin.handle_get_node_files,
        WS.GET_NODE_DIFF: FilesMixin.handle_get_node_diff,
        WS.GET_FILE_CONTENT: FilesMixin.handle_get_file_content,
        WS.GET_NODE_PROCESSES: ProcessesMixin.handle_get_node_processes,
        WS.KILL_PROCESS: ProcessesMixin.handle_kill_process,
        WS.KILL_ALL_PROCESSES: ProcessesMixin.handle_kill_all_processes,
        WS.DELETE_NODE: NodesMixin.handle_delete_node,
        WS.GET_REPO_INFO: RepoMixin.handle_get_repo_info,
        WS.LIST_BRANCHES: RepoMixin.handle_list_branches,
        WS.MERGE_TO_BRANCH: RepoMixin.handle_merge_to_branch,
        WS.OPEN_REPO: TreesMixin.handle_open_repo,
        WS.UPDATE_BASE: RepoMixin.handle_update_base,
    }


__all__ = ["ConnectionHandler", "_active_streams", "list_providers"]
