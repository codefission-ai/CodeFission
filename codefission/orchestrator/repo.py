"""Repo operations — open repo (find/create tree), update base.

open_repo: finds an existing tree for a repo+commit, or creates one.
update_base: changes a tree's base branch/commit (only before children exist).
"""

from __future__ import annotations

from pathlib import Path

from models import Tree, UpdateBaseResult
from store.trees import (
    get_tree,
    get_node,
    update_node,
    update_tree,
    find_tree,
)
from store.git import (
    _run_git,
    compute_repo_id,
    detect_repo_name,
    create_protective_ref,
    list_branches as ws_list_branches,
)
from config import get_project_path, set_project_path


class RepoMixin:
    """Repo operations — open_repo, update_base."""

    # ── Data accessors (thin wrappers over store) ────────────────────

    async def get_repo_info(self, repo_path=None):
        from store.git import get_repo_info
        return await get_repo_info(repo_path)

    async def list_branches(self):
        return await ws_list_branches()

    def detect_repo_name(self, repo_path) -> str:
        return detect_repo_name(repo_path)

    async def run_git(self, repo_path, *args, check=True):
        return await _run_git(repo_path, *args, check=check)

    async def create_protective_ref(self, tree_id: str, commit_sha: str):
        return await create_protective_ref(tree_id, commit_sha)

    # ── Operations ───────────────────────────────────────────────────

    async def open_repo(
        self,
        repo_id: str,
        repo_path: str,
        base_commit: str,
        base_branch: str = "main",
        repo_name: str | None = None,
    ) -> Tree:
        """Find or create a tree for a given repo+commit combination.

        If a tree already exists for repo_id+base_commit, returns it
        (updating repo_path if it changed). Otherwise creates a new one.
        """
        existing = await find_tree(repo_id, base_commit, repo_path)

        if existing:
            # Update repo_path if it has moved
            if existing.repo_path != repo_path:
                await update_tree(existing.id, repo_path=repo_path)
                existing = await get_tree(existing.id)
            return existing

        # Create new tree
        rname = repo_name or (Path(repo_path).name if repo_path else "untitled")
        tree, root = await self.create_tree(
            rname,
            base_branch=base_branch,
            repo_id=repo_id,
            repo_path=repo_path,
            repo_name=rname,
        )
        return tree

    async def update_base(
        self,
        tree_id: str,
        new_path: str | None = None,
        new_branch: str | None = None,
        new_commit: str | None = None,
        repo_path_context: Path | None = None,
    ) -> UpdateBaseResult:
        """Update a tree's repo_path, base_branch, and/or base_commit.

        Only allowed when root has no children.
        If a tree already exists for the resolved (repo_id, commit),
        returns existing_tree_id so the frontend can switch to it.

        Raises ValueError on validation failures.
        """
        tree = await get_tree(tree_id)
        if not tree:
            raise ValueError("Tree not found")

        # Guard: changes only allowed when tree has no children
        if tree.root_node_id:
            root = await get_node(tree.root_node_id)
            if root and root.children_ids:
                raise ValueError("Cannot change base after conversations have started")

        extra_branches: list[str] | None = None

        # If repo_path changed, validate and re-resolve repo context
        if new_path and new_path != tree.repo_path:
            repo_path = Path(new_path)
            if not repo_path.is_dir():
                raise ValueError(f"Not a directory: {new_path}")
            # Check it's a git repo
            rc, _, _ = await _run_git(repo_path, "rev-parse", "--git-dir", check=False)
            if rc != 0:
                raise ValueError(f"Not a git repo: {new_path}")
            set_project_path(repo_path)
            new_repo_id = await compute_repo_id(repo_path)
            new_repo_name = detect_repo_name(repo_path)
            extra_branches = await ws_list_branches()

            # Default branch/commit from the new repo if not explicitly given
            if not new_branch:
                _, detected_branch, _ = await _run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
                new_branch = detected_branch.strip()
            if not new_commit:
                _, head_sha, _ = await _run_git(repo_path, "rev-parse", "HEAD", check=False)
                new_commit = head_sha.strip()
        else:
            new_repo_id = tree.repo_id
            new_repo_name = tree.repo_name

        project_path = get_project_path()
        target_branch = new_branch or tree.base_branch

        # Resolve commit
        if new_commit:
            rc, full_sha, _ = await _run_git(project_path, "rev-parse", "--verify", new_commit, check=False)
            if rc != 0:
                raise ValueError(f"Commit {new_commit} not found")
            resolved_sha = full_sha.strip()
        else:
            rc, head_sha, _ = await _run_git(project_path, "rev-parse", target_branch, check=False)
            if rc != 0:
                raise ValueError(f"Branch {target_branch} not found")
            resolved_sha = head_sha.strip()

        # Check if a different tree already exists for this (repo_id, commit)
        if new_repo_id:
            existing = await find_tree(new_repo_id, resolved_sha, new_path)
            if existing and existing.id != tree_id:
                return UpdateBaseResult(
                    tree=existing,
                    existing_tree_id=existing.id,
                    branches=extra_branches,
                )

        # Update current tree
        update_kwargs: dict = {"base_commit": resolved_sha}
        if new_branch:
            update_kwargs["base_branch"] = new_branch
        if new_path and new_path != tree.repo_path:
            update_kwargs["repo_path"] = new_path
            update_kwargs["repo_id"] = new_repo_id
            update_kwargs["repo_name"] = new_repo_name
        await update_tree(tree_id, **update_kwargs)

        if tree.root_node_id:
            await update_node(tree.root_node_id, git_commit=resolved_sha)

        await create_protective_ref(tree_id, resolved_sha)

        updated = await get_tree(tree_id)
        return UpdateBaseResult(
            tree=updated,
            branches=extra_branches,
        )

