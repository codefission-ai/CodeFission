"""Node handlers — branch, get node details, delete node + subtree."""

from events import WS


class NodesMixin:

    async def handle_branch(self, data: dict):
        parent_id = data["parent_id"]
        label = data.get("label", "")
        node = await self.orch.branch(parent_id, label)
        await self.send(WS.NODE_CREATED, node=node.model_dump())

    async def handle_get_node(self, data: dict):
        node_id = data["node_id"]
        node = await self.orch.get_node(node_id)
        if node:
            await self.send(WS.NODE_DATA, node=node.model_dump())

    async def handle_delete_node(self, data: dict):
        from handlers import _active_streams

        node_id = data["node_id"]
        print(f"DELETE_NODE received: {node_id}")

        node = await self.orch.get_node(node_id)
        if not node:
            print(f"DELETE_NODE: node {node_id} not found")
            await self.send(WS.ERROR, error="Node not found")
            return

        await self._set_context_for_tree(node.tree_id)
        self.orch._active_streams = _active_streams

        try:
            result = await self.orch.delete_node(node_id)
            print(f"DELETE_NODE: deleted {result.deleted_ids}")
            await self.send(WS.NODES_DELETED,
                            deleted_ids=result.deleted_ids,
                            updated_nodes=[n.model_dump() for n in result.updated_nodes])
        except ValueError as e:
            print(f"DELETE_NODE ERROR: {e}")
            await self.send(WS.ERROR, error=str(e))
