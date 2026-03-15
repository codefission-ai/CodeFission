"""Tree handlers — open repo, list/create/load/delete trees.

open_repo is the first message the browser sends — it finds or creates
a tree for the given repo+commit. load_tree fetches all nodes and
reconnects any active streams (for browser refresh during streaming).
"""

import json
import logging

from events import WS
from store.trees import (
    list_trees, get_tree, get_all_nodes,
    delete_tree, find_tree,
)
from store.settings import (
    get_setting, set_setting, get_global_defaults,
)
from store.git import (
    check_staleness,
    _worktrees_dir,
    detect_repo_name,
    _run_git,
    create_protective_ref,
    get_repo_info as ws_get_repo_info,
    list_branches as ws_list_branches,
)
from store.processes import list_tree_processes

log = logging.getLogger(__name__)


class TreesMixin:

    async def handle_open_repo(self, data: dict):
        """Open a repo: find or create tree for the given repo_id + head_commit."""
        from pathlib import Path
        from config import set_project_path

        repo_path_str = data.get("repo_path") or (str(self.repo_path) if self.repo_path else None)
        repo_id = data.get("repo_id") or self.repo_id
        head_commit = data.get("head_commit") or self.head_commit

        if not repo_path_str or not repo_id or not head_commit:
            await self.send(WS.ERROR, error="Missing repo context")
            return

        repo_path = Path(repo_path_str)
        if not repo_path.is_dir():
            await self.send(WS.ERROR, error=f"Not a directory: {repo_path}")
            return

        # Update connection state
        self.repo_path = repo_path
        self.repo_id = repo_id
        self.head_commit = head_commit
        set_project_path(repo_path)

        repo_name = detect_repo_name(repo_path)

        # Find existing tree for this repo+commit
        tree = await find_tree(repo_id, head_commit, str(repo_path))

        if not tree:
            # Create new tree
            try:
                _, actual_branch, _ = await _run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
                tree, _root = await self.orch.create_tree(
                    repo_name, base_branch=actual_branch,
                    repo_id=repo_id, repo_path=str(repo_path), repo_name=repo_name,
                )
                if tree.base_commit:
                    await create_protective_ref(tree.id, tree.base_commit)
            except Exception as e:
                log.warning("Auto-create tree failed: %s", e)
                await self.send(WS.ERROR, error=f"Failed to create tree: {e}")
                return

        # Load tree data
        nodes = await get_all_nodes(tree.id)
        staleness = {"stale": False, "commits_behind": 0}
        if tree.base_commit:
            staleness = await check_staleness(tree.base_branch, tree.base_commit)

        info = await ws_get_repo_info(repo_path)
        branches = await ws_list_branches()

        await self.send(WS.REPO_OPENED, **info,
                        tree=tree.model_dump(),
                        nodes=[n.model_dump() for n in nodes],
                        staleness=staleness,
                        branches=branches,
                        repo_id=repo_id, repo_name=repo_name)

    async def handle_list_trees(self, data: dict):  # noqa: ARG002
        from handlers.settings import list_providers

        trees = await list_trees()
        last_tree_id = await get_setting("last_tree_id")
        raw = await get_setting("expanded_nodes")
        expanded_nodes = json.loads(raw) if raw else {}
        raw_cs = await get_setting("collapsed_subtrees")
        collapsed_subtrees = json.loads(raw_cs) if raw_cs else {}
        defaults = await get_global_defaults()
        await self.send(WS.TREES, trees=[t.model_dump() for t in trees],
                        last_tree_id=last_tree_id, expanded_nodes=expanded_nodes,
                        collapsed_subtrees=collapsed_subtrees,
                        global_defaults=defaults, providers=list_providers())

    async def handle_create_tree(self, data: dict):
        name = data.get("name", "Untitled")
        base_branch = data.get("base_branch", "main")
        try:
            tree, root = await self.orch.create_tree(
                name, base_branch=base_branch,
                repo_id=self.repo_id,
                repo_path=str(self.repo_path) if self.repo_path else None,
                repo_name=detect_repo_name(self.repo_path) if self.repo_path else None,
            )
            await self.send(WS.TREE_CREATED, tree=tree.model_dump(), root=root.model_dump())
        except Exception as e:
            await self.send(WS.ERROR, error=f"Failed to create tree: {e}")

    async def handle_load_tree(self, data: dict):
        from handlers.connection import _active_streams

        tree_id = data["tree_id"]
        await self._set_context_for_tree(tree_id)
        tree = await get_tree(tree_id)
        nodes = await get_all_nodes(tree_id)

        # Scan for running processes across all node workspaces
        node_processes = {}
        if tree and tree.root_node_id:
            wt_dir = _worktrees_dir()
            if wt_dir.exists():
                raw = list_tree_processes(wt_dir)
                for nid, procs in raw.items():
                    node_processes[nid] = [
                        {"pid": p.pid, "command": p.command, "ports": p.ports}
                        for p in procs
                    ]

        # Check staleness
        staleness = {"stale": False, "commits_behind": 0}
        if tree and tree.base_commit:
            staleness = await check_staleness(tree.base_branch, tree.base_commit)

        await self.send(
            WS.TREE_LOADED,
            tree=tree.model_dump() if tree else None,
            nodes=[n.model_dump() for n in nodes],
            node_processes=node_processes,
            staleness=staleness,
        )

        # Reconnect any active streams for this tree to the new connection.
        for nid, info in _active_streams.items():
            if info.tree_id == tree_id and info.status == "active":
                info.send_fn = self.send
                await self.send(WS.STATUS, node_id=nid, status="active")
                if info.text:
                    await self.send(WS.CHUNK, node_id=nid, text=info.text)

    async def handle_delete_tree(self, data: dict):
        tree_id = data["tree_id"]
        await self._set_context_for_tree(tree_id)
        last = await get_setting("last_tree_id")
        if last == tree_id:
            await set_setting("last_tree_id", None)
        await delete_tree(tree_id)
        await self.send(WS.TREE_DELETED, tree_id=tree_id)
