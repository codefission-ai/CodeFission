"""Process handlers — list running processes, kill by PID, kill all.

Also handles worktree cleanup after processes exit (removes ephemeral
worktrees that are no longer needed).
"""

import logging

from events import WS

log = logging.getLogger(__name__)


class ProcessesMixin:

    async def _send_tree_processes(self):
        """Scan and broadcast tree-wide process state so the UI stays in sync."""
        try:
            tree_procs = self.orch.scan_tree_processes()
            await self.send("tree_node_processes", tree_node_processes=tree_procs)
        except Exception:
            log.debug("Tree-wide process scan failed", exc_info=True)

    async def _resolve_node_workspace(self, node_id: str) -> tuple:
        """Resolve workspace path for a node. Returns (node, tree, workspace) or sends error."""
        node = await self.orch.get_node(node_id)
        if not node:
            await self.send(WS.ERROR, error="Node not found")
            return None, None, None
        tree = await self.orch.get_tree(node.tree_id)
        if not tree:
            await self.send(WS.ERROR, error="Tree not found")
            return None, None, None
        await self._set_context_for_tree(node.tree_id)
        ws_path = self.orch.resolve_node_workspace(tree.root_node_id, node_id)
        return node, tree, ws_path

    async def handle_get_node_processes(self, data: dict):
        node_id = data["node_id"]
        _, _, ws_path = await self._resolve_node_workspace(node_id)
        if not ws_path:
            return
        procs = self.orch.list_node_processes(ws_path)
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
        self.orch.kill_node_process(pid, ws_path)
        # Broadcast tree-wide process state
        await self._send_tree_processes()
        # If no more processes on this node, clean up the worktree
        procs = self.orch.list_node_processes(ws_path)
        if not procs and tree and node:
            await self.orch.cleanup_node_worktree(node, tree)

    async def handle_kill_all_processes(self, data: dict):
        node_id = data["node_id"]
        node, tree, ws_path = await self._resolve_node_workspace(node_id)
        if not ws_path:
            return
        self.orch.kill_all_node_processes(ws_path)
        # Broadcast tree-wide process state
        await self._send_tree_processes()
        # If no more processes on this node, clean up the worktree
        procs = self.orch.list_node_processes(ws_path)
        if not procs and tree and node:
            await self.orch.cleanup_node_worktree(node, tree)
