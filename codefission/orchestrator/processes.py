"""Process management — list, kill, and clean up node processes.

Also handles worktree cleanup after processes exit and post-chat
process scanning / worktree removal.
"""

from __future__ import annotations

import logging
from pathlib import Path

from models import Node, Tree
from store.trees import get_node, get_tree, update_node
from store.git import (
    resolve_workspace,
    remove_worktree, remove_worktree_and_branch,
    _worktrees_dir,
)
from store.processes import (
    list_processes,
    list_tree_processes,
    kill_process,
    kill_all_in_workspace,
    kill_process_tree,
)

log = logging.getLogger(__name__)


class ProcessesMixin:
    """Process management operations for the Orchestrator."""

    def get_worktrees_dir(self) -> Path:
        """Return the worktrees directory path."""
        return _worktrees_dir()

    def list_node_processes(self, workspace: Path) -> list:
        """List processes running under a node's workspace."""
        return list_processes(workspace)

    def list_tree_processes(self, worktrees_dir: Path) -> dict:
        """Scan all node workspaces under a tree and return grouped processes."""
        return list_tree_processes(worktrees_dir)

    def kill_node_process(self, pid: int, workspace: Path) -> bool:
        """Kill a single process by PID (verified against workspace)."""
        return kill_process(pid, workspace)

    def kill_all_node_processes(self, workspace: Path) -> int:
        """Kill all processes in a workspace. Returns count killed."""
        return kill_all_in_workspace(workspace)

    def kill_sdk_process_tree(self, pid: int) -> None:
        """Kill an SDK subprocess and all its descendants."""
        kill_process_tree(pid)

    def resolve_node_workspace(self, root_node_id: str | None, node_id: str) -> Path:
        """Resolve the workspace path for a node."""
        return resolve_workspace(root_node_id, node_id)

    async def cleanup_node_worktree(self, node: Node, tree: Tree) -> None:
        """Remove a node's worktree after all processes have exited."""
        try:
            node_id = node.id
            if not node.parent_id:
                return  # Never remove root workspace
            parent = await get_node(node.parent_id)
            if parent and node.git_commit and node.git_commit == parent.git_commit:
                await remove_worktree_and_branch(tree.root_node_id, node_id)
                await update_node(node_id, git_branch=None)
            else:
                await remove_worktree(tree.root_node_id, node_id)
        except Exception:
            log.debug("Worktree cleanup failed for %s", node.id, exc_info=True)

    async def post_chat_cleanup(
        self, node_id: str, files_changed: int
    ) -> dict[str, list] | None:
        """Post-chat process scan and worktree cleanup.

        Returns a dict of node processes if any are running, otherwise
        removes the ephemeral worktree and returns None.
        """
        node = await get_node(node_id)
        if not node:
            return None

        tree = await get_tree(node.tree_id)
        if not tree:
            return None

        wt_dir = _worktrees_dir()
        tree_procs_raw = list_tree_processes(wt_dir) if wt_dir.exists() else {}
        this_node_procs = tree_procs_raw.get(node_id, [])

        if this_node_procs:
            return {
                "processes": [
                    {"pid": p.pid, "command": p.command, "ports": p.ports}
                    for p in this_node_procs
                ]
            }

        # No running processes — remove ephemeral worktree
        try:
            if files_changed == 0 and node.parent_id:
                await remove_worktree_and_branch(tree.root_node_id, node_id)
                await update_node(node_id, git_branch=None)
            else:
                await remove_worktree(tree.root_node_id, node_id)
        except Exception:
            log.debug("Ephemeral worktree removal failed for %s", node_id, exc_info=True)

        return None

    async def post_error_cleanup(self, node_id: str) -> None:
        """Clean up worktree after a chat error, if no processes are running."""
        node = await get_node(node_id)
        if not node:
            return
        tree = await get_tree(node.tree_id)
        if not tree:
            return
        ws_path = resolve_workspace(tree.root_node_id, node_id)
        procs = list_processes(ws_path) if ws_path.exists() else []
        if not procs:
            await remove_worktree(tree.root_node_id, node_id)

    def scan_tree_processes(self) -> dict[str, list[dict]]:
        """Scan for all processes across all node workspaces and return formatted dict."""
        wt_dir = _worktrees_dir()
        if not wt_dir.exists():
            return {}
        raw = list_tree_processes(wt_dir)
        result = {}
        for nid, procs in raw.items():
            result[nid] = [
                {"pid": p.pid, "command": p.command, "ports": p.ports}
                for p in procs
            ]
        return result
