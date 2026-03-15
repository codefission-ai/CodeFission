"""Node operation handler methods — mixin for ConnectionHandler."""

from events import WS
from store.trees import get_node


class NodesMixin:

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

    async def handle_delete_node(self, data: dict):
        from handlers.connection import _active_streams

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
