"""Process management handler methods — mixin for ConnectionHandler."""

import logging

from events import WS
from services.trees import get_node, get_tree, update_node
from services.workspace import (
    resolve_workspace,
    remove_worktree, remove_worktree_and_branch,
    _worktrees_dir,
)
from services.process_service import list_processes, list_tree_processes, kill_process, kill_all_in_workspace

log = logging.getLogger(__name__)


class ProcessesMixin:

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
