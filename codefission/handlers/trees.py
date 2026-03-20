"""Tree handlers — open repo, list/create/load/delete trees.

open_repo is the first message the browser sends — it finds or creates
a tree for the given repo+commit. load_tree fetches all nodes and
reconnects any active streams (for browser refresh during streaming).
"""

import logging

from events import WS

log = logging.getLogger(__name__)


class TreesMixin:

    async def handle_open_repo(self, data: dict):
        """Open a repo: find or create tree for the given repo_id + head_commit.

        If repo_id / head_commit are not provided, they are auto-detected from
        the git repo at repo_path — this supports the "New Project" sidebar flow.
        """
        from pathlib import Path
        from config import set_project_path

        repo_path_str = data.get("repo_path") or (str(self.repo_path) if self.repo_path else None)
        if not repo_path_str:
            await self.send(WS.ERROR, error="Missing repo path")
            return

        repo_path = Path(repo_path_str)
        if not repo_path.is_dir():
            await self.send(WS.ERROR, error=f"Not a directory: {repo_path}")
            return

        # Only reuse connection state if the repo_path hasn't changed.
        # If this is a NEW repo_path, we must auto-detect fresh repo_id/head_commit
        # — never reuse stale values from the previous repo.
        same_repo = self.repo_path and repo_path == self.repo_path
        repo_id = data.get("repo_id") or (self.repo_id if same_repo else None)
        head_commit = data.get("head_commit") or (self.head_commit if same_repo else None)

        # Auto-detect repo_id and head_commit from the git repo
        if not repo_id or not head_commit:
            try:
                set_project_path(repo_path)
                # repo_id = SHA of the initial commit (repo identity)
                rc, first_sha, _ = await self.orch.run_git(
                    repo_path, "rev-list", "--max-parents=0", "HEAD", check=False,
                )
                if rc != 0 or not first_sha.strip():
                    # Not a git repo — auto-initialize
                    log.info("Auto-initializing git repo at %s", repo_path)
                    from store.git import init_git_repo
                    await init_git_repo(repo_path)
                    # Re-detect after init
                    rc, first_sha, _ = await self.orch.run_git(
                        repo_path, "rev-list", "--max-parents=0", "HEAD", check=False,
                    )
                    if rc != 0 or not first_sha.strip():
                        await self.send(WS.ERROR, error=f"Failed to initialize git repo: {repo_path}")
                        return
                repo_id = repo_id or first_sha.strip().split("\n")[0]
                # head_commit = current HEAD
                _, head_sha, _ = await self.orch.run_git(repo_path, "rev-parse", "HEAD")
                head_commit = head_commit or head_sha.strip()
            except Exception as e:
                log.warning("Failed to detect repo context: %s", e)
                await self.send(WS.ERROR, error=f"Failed to read git repo: {e}")
                return

        # Update connection state
        self.repo_path = repo_path
        self.repo_id = repo_id
        self.head_commit = head_commit
        set_project_path(repo_path)

        repo_name = self.orch.detect_repo_name(repo_path)

        # Find existing tree for this repo+commit
        tree = await self.orch.find_tree(repo_id, head_commit, str(repo_path))

        if not tree:
            # Create new tree
            try:
                _, actual_branch, _ = await self.orch.run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
                tree, _root = await self.orch.create_tree(
                    "Untitled", base_branch=actual_branch,
                    repo_id=repo_id, repo_path=str(repo_path), repo_name=repo_name,
                )
                if tree.base_commit:
                    await self.orch.create_protective_ref(tree.id, tree.base_commit)
            except Exception as e:
                log.warning("Auto-create tree failed: %s", e)
                await self.send(WS.ERROR, error=f"Failed to create tree: {e}")
                return

        # Load tree data
        nodes = await self.orch.get_all_nodes(tree.id)
        info = await self.orch.get_repo_info(repo_path)
        branches = await self.orch.list_branches()

        await self.send(WS.REPO_OPENED, **info,
                        tree=tree.model_dump(),
                        nodes=[n.model_dump() for n in nodes],
                        branches=branches,
                        repo_id=repo_id, repo_name=repo_name)

    async def handle_list_trees(self, data: dict):  # noqa: ARG002
        from handlers.settings import list_providers

        trees = await self.orch.list_trees()
        last_tree_id = await self.orch.get_setting("last_tree_id")
        defaults = await self.orch.get_global_defaults()
        providers = await list_providers()
        await self.send(WS.TREES, trees=[t.model_dump() for t in trees],
                        last_tree_id=last_tree_id,
                        global_defaults=defaults, providers=providers)

    async def handle_create_tree(self, data: dict):
        name = data.get("name", "Untitled")
        base_branch = data.get("base_branch", "main")
        from_node_id = data.get("from_node_id")
        base_commit = data.get("base_commit")

        # Allow overriding repo context from the message (e.g. "new tree from node")
        repo_id = data.get("repo_id") or self.repo_id
        repo_path_str = data.get("repo_path") or (str(self.repo_path) if self.repo_path else None)
        repo_name = None
        if repo_path_str:
            from pathlib import Path as _Path
            rp = _Path(repo_path_str)
            repo_name = self.orch.detect_repo_name(rp) if rp.is_dir() else None

        try:
            tree, root = await self.orch.create_tree(
                name, base_branch=base_branch,
                repo_id=repo_id,
                repo_path=repo_path_str,
                repo_name=repo_name,
                from_node_id=from_node_id,
                base_commit=base_commit,
            )
            await self.send(WS.TREE_CREATED, tree=tree.model_dump(), root=root.model_dump())
        except Exception as e:
            await self.send(WS.ERROR, error=f"Failed to create tree: {e}")

    async def handle_load_tree(self, data: dict):
        from handlers import _active_streams

        tree_id = data["tree_id"]
        await self._set_context_for_tree(tree_id)
        tree = await self.orch.get_tree(tree_id)
        nodes = await self.orch.get_all_nodes(tree_id)

        # Scan for running processes across all node workspaces
        node_processes = {}
        if tree and tree.root_node_id:
            node_processes = self.orch.scan_tree_processes()

        # Include branches for this tree's repo (so switching projects refreshes branches)
        branches = await self.orch.list_branches()

        await self.send(
            WS.TREE_LOADED,
            tree=tree.model_dump() if tree else None,
            nodes=[n.model_dump() for n in nodes],
            node_processes=node_processes,
            branches=branches,
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
        last = await self.orch.get_setting("last_tree_id")
        if last == tree_id:
            await self.orch.set_setting("last_tree_id", None)
        await self.orch.remove_tree(tree_id)
        await self.send(WS.TREE_DELETED, tree_id=tree_id)
