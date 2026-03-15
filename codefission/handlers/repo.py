"""Repo handlers — repo info, list branches, merge to branch, update base."""

from pathlib import Path

from events import WS


class RepoMixin:

    async def handle_get_repo_info(self, data: dict):  # noqa: ARG002
        info = await self.orch.get_repo_info()
        await self.send(WS.REPO_INFO, **info)

    async def handle_list_branches(self, data: dict):  # noqa: ARG002
        branches = await self.orch.list_branches()
        await self.send(WS.BRANCHES, branches=branches)

    async def handle_merge_to_branch(self, data: dict):
        node_id = data["node_id"]
        target_branch = data["target_branch"]
        # Set context for the node's tree
        node = await self.orch.get_node(node_id)
        if node:
            await self._set_context_for_tree(node.tree_id)
        result = await self.orch.merge_to_branch(node_id, target_branch)
        await self.send(WS.MERGE_RESULT, node_id=node_id, **result)

    async def handle_update_base(self, data: dict):
        tree_id = data["tree_id"]
        new_path = data.get("repo_path")
        new_branch = data.get("base_branch")
        new_commit = data.get("base_commit")

        # Set context from existing tree if no new path
        if not new_path:
            await self._set_context_for_tree(tree_id)
        else:
            tree = await self.orch.get_tree(tree_id)
            if tree and new_path == tree.repo_path:
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
