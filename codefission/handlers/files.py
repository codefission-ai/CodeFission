"""File operation handler methods — mixin for ConnectionHandler."""

from events import WS
from services.trees import get_node


class FilesMixin:

    async def handle_get_node_files(self, data: dict):
        node_id = data["node_id"]
        node = await get_node(node_id)
        if not node:
            await self.send(WS.ERROR, error="Node not found")
            return
        await self._set_context_for_tree(node.tree_id)
        try:
            result = await self.orch.list_node_files(node_id)
            await self.send(WS.NODE_FILES, node_id=result.node_id, files=result.files)
        except ValueError as e:
            await self.send(WS.ERROR, error=str(e))

    async def handle_get_node_diff(self, data: dict):
        node_id = data["node_id"]
        node = await get_node(node_id)
        if not node:
            await self.send(WS.ERROR, error="Node not found")
            return
        await self._set_context_for_tree(node.tree_id)
        try:
            result = await self.orch.get_node_diff(node_id)
            await self.send(WS.NODE_DIFF, node_id=result.node_id, diff=result.diff)
        except ValueError as e:
            await self.send(WS.ERROR, error=str(e))

    async def handle_get_file_content(self, data: dict):
        node_id = data["node_id"]
        file_path = data["file_path"]
        node = await get_node(node_id)
        if not node:
            await self.send(WS.ERROR, error="Node not found")
            return
        await self._set_context_for_tree(node.tree_id)
        try:
            result = await self.orch.read_node_file(node_id, file_path)
            await self.send(WS.FILE_CONTENT, node_id=result.node_id, file_path=result.file_path, content=result.content)
        except Exception as e:
            await self.send(WS.ERROR, error=f"Cannot read file: {e}")
