"""Tree operations — create tree (with git ref), delete node + subtree.

create_tree: resolves git HEAD, creates DB records, sets up protective ref.
delete_node: checks for active streams, removes worktrees, cleans up
  expanded/collapsed settings, cascades through subtree.
"""

from __future__ import annotations

import json
import logging

from models import Node, Tree, DeleteNodeResult
from store.trees import (
    create_tree as _create_tree,
    get_tree,
    get_node,
    get_all_nodes,
    update_node,
    update_tree,
    delete_subtree,
    list_trees as _list_trees,
    delete_tree as _delete_tree,
    find_tree as _find_tree,
)
from store.settings import (
    get_setting,
    set_setting,
)
from store.git import (
    resolve_workspace,
    remove_worktree_and_branch,
    _run_git,
    create_protective_ref,
)
from store.processes import kill_all_in_workspace
from config import get_project_path

log = logging.getLogger(__name__)


class TreesMixin:
    """Tree CRUD and delete_node operations."""

    # ── Data accessors (thin wrappers over store) ────────────────────

    async def get_tree(self, tree_id: str) -> Tree | None:
        return await get_tree(tree_id)

    async def get_node(self, node_id: str) -> Node | None:
        return await get_node(node_id)

    async def get_all_nodes(self, tree_id: str) -> list[Node]:
        return await get_all_nodes(tree_id)

    async def list_trees(self, repo_id: str | None = None) -> list[Tree]:
        return await _list_trees(repo_id)

    async def remove_tree(self, tree_id: str) -> None:
        await _delete_tree(tree_id)

    async def find_tree(self, repo_id: str, base_commit: str, repo_path: str | None = None) -> Tree | None:
        return await _find_tree(repo_id, base_commit, repo_path)

    async def update_tree(self, tree_id: str, **kwargs) -> None:
        await update_tree(tree_id, **kwargs)

    async def update_node(self, node_id: str, **kwargs) -> None:
        await update_node(node_id, **kwargs)

    # ── Tree creation ────────────────────────────────────────────────

    async def create_tree(
        self,
        name: str,
        base_branch: str = "main",
        repo_id: str | None = None,
        repo_path: str | None = None,
        repo_name: str | None = None,
    ) -> tuple[Tree, Node]:
        """Create a tree + root node from the user's repo. Returns (tree, root_node).

        No cloning or setup_repo — the repo is the project path itself.
        """
        project_path = get_project_path()
        # Resolve HEAD of the base_branch in the user's repo
        _, head_sha, _ = await _run_git(project_path, "rev-parse", base_branch)
        _, actual_branch, _ = await _run_git(project_path, "rev-parse", "--abbrev-ref", base_branch, check=False)

        tree, root = await _create_tree(
            name, base_branch=actual_branch, base_commit=head_sha,
            repo_id=repo_id, repo_path=repo_path, repo_name=repo_name,
        )
        await update_node(root.id, git_branch=actual_branch, git_commit=head_sha)

        # Protective ref prevents GC of the base commit
        await create_protective_ref(tree.id, head_sha)

        root = await get_node(root.id)
        return tree, root

    async def delete_node(self, node_id: str) -> DeleteNodeResult:
        """Delete a node and its subtree.

        Checks for active streams, kills processes, removes worktrees/branches,
        cleans up expanded_nodes and collapsed_subtrees settings.

        Raises ValueError if node not found, is root, or has active streams.
        """
        node = await get_node(node_id)
        if not node:
            raise ValueError("Node not found")
        if not node.parent_id:
            raise ValueError("Cannot delete root node")

        # Check no node in subtree is actively streaming
        stack = [node_id]
        while stack:
            nid = stack.pop()
            if nid in self._active_streams and self._active_streams[nid].status == "active":
                raise ValueError("Cannot delete a node that is streaming. Cancel it first.")
            n = await get_node(nid)
            if n:
                stack.extend(n.children_ids)

        tree = await get_tree(node.tree_id)
        deleted_ids, updated_nodes = await delete_subtree(node_id)

        # Kill processes and clean up git worktrees/branches for deleted nodes
        if tree:
            for did in deleted_ids:
                try:
                    ws_path = resolve_workspace(tree.root_node_id, did)
                    if ws_path.exists():
                        kill_all_in_workspace(ws_path)
                    await remove_worktree_and_branch(tree.root_node_id, did)
                except Exception:
                    log.debug("Cleanup failed for deleted node %s", did, exc_info=True)

        # Clean up expanded_nodes and collapsed_subtrees settings
        deleted_set = set(deleted_ids)
        raw_exp = await get_setting("expanded_nodes")
        if raw_exp:
            exp_map = json.loads(raw_exp)
            cleaned = {k: v for k, v in exp_map.items() if k not in deleted_set}
            if len(cleaned) != len(exp_map):
                await set_setting("expanded_nodes", json.dumps(cleaned))
        raw_cs = await get_setting("collapsed_subtrees")
        if raw_cs:
            cs_map = json.loads(raw_cs)
            cleaned = {k: v for k, v in cs_map.items() if k not in deleted_set}
            if len(cleaned) != len(cs_map):
                await set_setting("collapsed_subtrees", json.dumps(cleaned))

        return DeleteNodeResult(deleted_ids=deleted_ids, updated_nodes=updated_nodes)
