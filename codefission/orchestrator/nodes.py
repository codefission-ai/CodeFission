"""Node operations — create branches (child nodes) from a parent."""

from __future__ import annotations

from models import Node
from store.trees import (
    get_node,
    create_child_node,
    update_node,
)


class NodesMixin:
    """Node operations — branch creation."""

    async def branch(
        self,
        parent_id: str,
        label: str = "",
        created_by: str = "human",
    ) -> Node:
        """Create a child node. Worktree is created on demand when chat starts."""
        node = await create_child_node(parent_id, label, created_by=created_by)

        parent = await get_node(parent_id)
        if parent:
            # Record branch name and inherit parent's commit (worktree created lazily)
            branch_name = f"ct-{node.id}"
            await update_node(node.id, git_branch=branch_name, git_commit=parent.git_commit)
            node = await get_node(node.id)

        return node
