"""File operations mixin for the Orchestrator."""

from __future__ import annotations

from services.trees import get_tree, get_node
from services.workspace import (
    resolve_workspace,
    list_files,
    list_files_from_commit,
    read_file,
    read_file_from_commit,
    get_diff,
    get_diff_from_commits,
    list_artifact_files,
)
from services.orchestrator.types import FileListResult, DiffResult, FileContentResult


class FilesMixin:
    """File operations — list, diff, read with worktree-or-git fallback."""

    async def list_node_files(self, node_id: str) -> FileListResult:
        """List files for a node, using worktree or git commit fallback."""
        node = await get_node(node_id)
        if not node:
            raise ValueError("Node not found")
        tree = await get_tree(node.tree_id)
        if not tree:
            raise ValueError("Tree not found")

        ws_path = resolve_workspace(tree.root_node_id, node_id)
        if ws_path.exists():
            files = await list_files(ws_path)
        elif node.git_commit:
            files = await list_files_from_commit(node.git_commit)
        else:
            files = []

        # Append persisted artifact files (deduplicated)
        artifact_files = list_artifact_files(node_id)
        if artifact_files:
            existing = set(files)
            files.extend(f for f in artifact_files if f not in existing)

        return FileListResult(node_id=node_id, files=files)

    async def get_node_diff(self, node_id: str) -> DiffResult:
        """Get diff for a node, using worktree or git commit fallback."""
        node = await get_node(node_id)
        if not node:
            raise ValueError("Node not found")
        tree = await get_tree(node.tree_id)
        if not tree:
            raise ValueError("Tree not found")

        ws_path = resolve_workspace(tree.root_node_id, node_id)
        parent_commit = None
        if node.parent_id:
            parent_node = await get_node(node.parent_id)
            if parent_node:
                parent_commit = parent_node.git_commit
        if ws_path.exists():
            diff = await get_diff(ws_path, parent_commit)
        elif node.git_commit:
            diff = await get_diff_from_commits(parent_commit, node.git_commit)
        else:
            diff = ""

        return DiffResult(node_id=node_id, diff=diff)

    async def read_node_file(self, node_id: str, file_path: str) -> FileContentResult:
        """Read file content for a node, using worktree or git commit fallback."""
        node = await get_node(node_id)
        if not node:
            raise ValueError("Node not found")
        tree = await get_tree(node.tree_id)
        if not tree:
            raise ValueError("Tree not found")

        ws_path = resolve_workspace(tree.root_node_id, node_id)
        if ws_path.exists():
            content = read_file(ws_path, file_path)
        elif node.git_commit:
            content = await read_file_from_commit(node.git_commit, file_path)
        else:
            raise FileNotFoundError(f"No worktree or commit for node {node_id}")

        return FileContentResult(node_id=node_id, file_path=file_path, content=content)

    # ── Convenience aliases for test compatibility ──────────────────

    async def list_files(self, node_id: str) -> list[str]:
        """Convenience: return just the file list (no wrapper)."""
        result = await self.list_node_files(node_id)
        return result.files

    async def get_diff(self, node_id: str) -> str:
        """Convenience: return just the diff string."""
        result = await self.get_node_diff(node_id)
        return result.diff

    async def read_file(self, node_id: str, file_path: str) -> str:
        """Convenience: return just the file content string."""
        result = await self.read_node_file(node_id, file_path)
        return result.content
