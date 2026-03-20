"""Tree operations — create tree (with git ref), delete node + subtree.

create_tree: resolves git HEAD, creates DB records, sets up protective ref.
delete_node: checks for active streams, removes worktrees, cascades
through subtree.
"""

from __future__ import annotations

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
        from_node_id: str | None = None,
        base_commit: str | None = None,
    ) -> tuple[Tree, Node]:
        """Create a tree + root node from the user's repo. Returns (tree, root_node).

        If from_node_id is provided, use that node's git_commit as the base
        commit instead of resolving from the branch HEAD. Also inherits the
        source tree's instructions (skill).
        """
        project_path = get_project_path()

        if from_node_id:
            source_node = await get_node(from_node_id)
            if not source_node or not source_node.git_commit:
                raise ValueError(f"Source node {from_node_id} not found or has no git commit")
            head_sha = source_node.git_commit
            # Inherit instructions and repo context from source tree
            source_tree = await get_tree(source_node.tree_id)
            # Use source tree's base_branch (e.g. "main"), not the node's
            # internal ct- branch name
            actual_branch = (source_tree.base_branch if source_tree else None) or base_branch
            skill = source_tree.skill if source_tree else ""
            if source_tree:
                repo_id = repo_id or source_tree.repo_id
                repo_path = repo_path or source_tree.repo_path
                repo_name = repo_name or source_tree.repo_name
        else:
            # Resolve commit: use explicit base_commit if provided (git graph),
            # otherwise resolve HEAD of base_branch
            if base_commit:
                head_sha = base_commit
            else:
                _, head_sha, _ = await _run_git(project_path, "rev-parse", base_branch)
            _, actual_branch, _ = await _run_git(project_path, "rev-parse", "--abbrev-ref", base_branch, check=False)
            skill = ""

        tree, root = await _create_tree(
            name, base_branch=actual_branch, base_commit=head_sha,
            repo_id=repo_id, repo_path=repo_path, repo_name=repo_name,
        )
        await update_node(root.id, git_branch=actual_branch, git_commit=head_sha)

        # Set inherited instructions
        if skill:
            await update_tree(tree.id, skill=skill)
            tree = await get_tree(tree.id)

        # Protective ref prevents GC of the base commit
        await create_protective_ref(tree.id, head_sha)

        root = await get_node(root.id)
        return tree, root

    async def delete_node(self, node_id: str) -> DeleteNodeResult:
        """Delete a node and its subtree.

        Checks for active streams, kills processes, removes worktrees/branches.

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

        return DeleteNodeResult(deleted_ids=deleted_ids, updated_nodes=updated_nodes)
