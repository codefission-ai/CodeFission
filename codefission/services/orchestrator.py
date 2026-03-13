"""Transport-agnostic orchestrator — business logic for tree/node/chat operations.

Both the WebSocket handler and future headless agents (shadow, CI) call into
this class instead of scattering logic across the transport layer.

Phase 1: all business logic lives here. Presenters (handlers.py, REST routes)
are thin dispatchers that call Orchestrator methods and format results for
their transport.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator

from models import Node, Tree
from services.tree_service import (
    create_tree as _create_tree,
    get_tree,
    get_node,
    get_all_nodes,
    create_child_node,
    update_node,
    update_tree,
    delete_subtree,
    get_global_defaults,
    get_setting,
    set_setting,
    resolve_tree_settings,
    find_tree,
    get_ancestor_chain,
)
from config import get_project_path, set_project_path
from services.workspace_service import (
    create_worktree,
    ensure_worktree,
    auto_commit,
    persist_artifacts,
    resolve_workspace,
    copy_session,
    _run_git,
    list_files,
    list_files_from_commit,
    read_file,
    read_file_from_commit,
    get_diff,
    get_diff_from_commits,
    list_artifact_files,
    remove_worktree,
    remove_worktree_and_branch,
    merge_to_branch as ws_merge_to_branch,
    check_staleness,
    compute_repo_id,
    detect_repo_name,
    list_branches as ws_list_branches,
    create_protective_ref,
)
from services.chat_service import stream_chat, TextDelta, ToolStart, ToolEnd, SessionInit
from services.process_service import (
    list_processes,
    kill_all_in_workspace,
    find_child_by_cwd,
    kill_process_tree,
)

log = logging.getLogger(__name__)


# ── Domain events (yielded by chat() async generator) ────────────────


@dataclass
class ChatNodeCreated:
    """A new child node was created for this chat."""
    node: Node
    after_id: str | None = None


@dataclass
class ChatCompleted:
    """Chat finished successfully."""
    result: ChatResult


# Re-export agentbridge events so presenters can use isinstance checks
# TextDelta, ToolStart, ToolEnd, SessionInit are imported above


# ── Result dataclasses ────────────────────────────────────────────────


@dataclass
class ChatContext:
    """Everything needed to start streaming a chat."""
    node_id: str
    node: Node
    workspace: Path
    sdk_message: str
    parent_session_id: str | None
    model: str
    max_turns: int
    auth_mode: str
    api_key: str
    after_id: str | None = None
    quoted_node_ids: list[str] = field(default_factory=list)


@dataclass
class ChatResult:
    """Returned after a successful chat completion."""
    node_id: str
    full_response: str
    git_commit: str | None = None
    files_changed: int = 0


@dataclass
class CancelResult:
    """Returned after a cancelled chat."""
    node_id: str
    saved_text: str
    active_tools: list[str] = field(default_factory=list)


@dataclass
class DeleteNodeResult:
    """Result of deleting a subtree."""
    deleted_ids: list[str]
    updated_nodes: list[Node]


@dataclass
class UpdateBaseResult:
    """Result of updating a tree's base branch/commit."""
    tree: Tree
    existing_tree_id: str | None = None
    staleness: dict = field(default_factory=lambda: {"stale": False, "commits_behind": 0})
    branches: list[str] | None = None


@dataclass
class FileListResult:
    """Result of listing files for a node."""
    node_id: str
    files: list[str]


@dataclass
class DiffResult:
    """Result of getting diff for a node."""
    node_id: str
    diff: str


@dataclass
class FileContentResult:
    """Result of reading file content for a node."""
    node_id: str
    file_path: str
    content: str


# ── Orchestrator ──────────────────────────────────────────────────────


class Orchestrator:
    """Business-logic coordinator — owns all domain logic.

    Methods perform the multi-step workflows that were previously inlined
    in ConnectionHandler. They return data; the caller decides how to
    deliver it (WebSocket, stdout, etc.).
    """

    def __init__(self):
        # Active streams registry — keyed by node_id, used for cancel and reconnect
        self._active_streams: dict[str, StreamState] = {}

    # ── Tree CRUD ─────────────────────────────────────────────────────

    async def create_tree(
        self,
        name: str,
        base_branch: str = "main",
        repo_id: str | None = None,
        repo_path: str | None = None,
        repo_name: str | None = None,
    ) -> tuple[Tree, Node]:
        """Create a tree + root node from the user's repo. Returns (tree, root_node).

        No cloning or setup_repo — the repo is the project path itself.
        """
        project_path = get_project_path()
        # Resolve HEAD of the base_branch in the user's repo
        _, head_sha, _ = await _run_git(project_path, "rev-parse", base_branch)
        _, actual_branch, _ = await _run_git(project_path, "rev-parse", "--abbrev-ref", base_branch, check=False)

        tree, root = await _create_tree(
            name, base_branch=actual_branch, base_commit=head_sha,
            repo_id=repo_id, repo_path=repo_path, repo_name=repo_name,
        )
        await update_node(root.id, git_branch=actual_branch, git_commit=head_sha)

        # Protective ref prevents GC of the base commit
        await create_protective_ref(tree.id, head_sha)

        root = await get_node(root.id)
        return tree, root

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

    async def prepare_draft(self, parent_id: str) -> Node:
        """Create a draft child node with workspace ready for file uploads.

        The draft is invisible in the tree UI (filtered by status='draft').
        When the user sends a message, prepare_chat reuses it instead of
        creating a new child.
        """
        parent = await get_node(parent_id)
        if not parent:
            raise ValueError(f"Parent node {parent_id} not found")

        tree = await get_tree(parent.tree_id)
        if not tree or not tree.root_node_id:
            raise ValueError("Tree not found")

        # Check for existing draft under this parent and reuse it
        from services.tree_service import get_drafts_for_parent
        existing = await get_drafts_for_parent(parent_id)
        if existing:
            return existing[0]

        # Create child node with status=draft
        node = await create_child_node(parent_id, label="", created_by="human")
        branch_name = f"ct-{node.id}"
        await update_node(node.id, status="draft", git_branch=branch_name, git_commit=parent.git_commit)

        # Ensure worktree exists so files can be uploaded
        await ensure_worktree(
            tree.root_node_id, node.id,
            parent_id, parent.git_commit,
        )

        return await get_node(node.id)

    async def discard_draft(self, tree_id: str, draft_node_id: str) -> None:
        """Delete a draft node and its workspace."""
        node = await get_node(draft_node_id)
        if not node or node.status != "draft" or node.tree_id != tree_id:
            return

        tree = await get_tree(tree_id)
        if not tree or not tree.root_node_id:
            return

        # Remove worktree + branch
        try:
            await remove_worktree_and_branch(tree.root_node_id, draft_node_id)
        except Exception:
            log.debug("Draft worktree removal failed for %s", draft_node_id, exc_info=True)

        # Delete the node from DB
        from services.tree_service import delete_single_node
        await delete_single_node(draft_node_id)

    # ── File quote context ────────────────────────────────────────────

    async def _build_file_quote_context(
        self,
        file_quotes: list[dict],
        tree: Tree,
        root_node_id: str | None,
    ) -> str:
        """Build prompt context from file-level quotes (files, folders, diff selections)."""
        MAX_FILE_SIZE = 50_000  # 50KB per file
        MAX_TOTAL = 200_000    # 200KB total
        parts = [
            "[System: The user has quoted specific content from other branches:\n"
        ]
        total = 0
        for fq in file_quotes:
            nid = fq["node_id"]
            qtype = fq["type"]
            qnode = await get_node(nid)
            ws_path = resolve_workspace(root_node_id, nid)
            node_label = qnode.label if qnode else nid[:8]

            # Build git ref metadata for the quoted node
            git_ref = ""
            if qnode and qnode.git_commit:
                branch = qnode.git_branch or f"ct-{nid}"
                git_ref = f", branch: {branch}, commit: {qnode.git_commit[:12]}"

            if qtype == "note":
                # Quote a sticky note's text content
                note_content = fq.get("content", "")
                if note_content and total + len(note_content) <= MAX_TOTAL:
                    total += len(note_content)
                    parts.append(f'\n--- Note ---\n')
                    parts.append(note_content)
                    parts.append("\n---\n")
                continue

            if qtype == "node":
                # Quote the node's conversation (user message + assistant response)
                if qnode:
                    node_parts = []
                    if qnode.user_message:
                        node_parts.append(f"User: {qnode.user_message}")
                    if qnode.assistant_response:
                        node_parts.append(f"Assistant: {qnode.assistant_response}")
                    if node_parts:
                        content = "\n\n".join(node_parts)
                        if len(content) > MAX_FILE_SIZE:
                            content = content[:MAX_FILE_SIZE] + "\n... [truncated]"
                        if total + len(content) <= MAX_TOTAL:
                            total += len(content)
                            parts.append(f'\n--- Node: "{node_label}" (node: {nid[:12]}{git_ref}) ---\n')
                            parts.append(content)
                            parts.append("\n---\n")

            elif qtype == "file":
                fpath = fq.get("path", "")
                selected_content = fq.get("content", "")
                if selected_content:
                    # Text selection within a file
                    if total + len(selected_content) <= MAX_TOTAL:
                        total += len(selected_content)
                        parts.append(f'\n--- File selection: {fpath} (from "{node_label}"{git_ref}) ---\n')
                        parts.append(selected_content)
                        parts.append("\n---\n")
                else:
                    # Full file quote — try filesystem first, fall back to git
                    content = None
                    full = ws_path / fpath
                    if full.exists() and full.is_file():
                        try:
                            content = full.read_text(errors="replace")
                        except Exception:
                            pass
                    elif qnode and qnode.git_commit:
                        try:
                            content = await read_file_from_commit(qnode.git_commit, fpath)
                        except Exception:
                            pass
                    if content is not None:
                        if len(content) > MAX_FILE_SIZE:
                            content = content[:MAX_FILE_SIZE] + "\n... [truncated]"
                        if total + len(content) > MAX_TOTAL:
                            parts.append(f'\n--- File: {fpath} (from "{node_label}"{git_ref}) --- [skipped: size limit]\n')
                            continue
                        total += len(content)
                        parts.append(f'\n--- File: {fpath} (from "{node_label}"{git_ref}) ---\n')
                        parts.append(content)
                        parts.append("\n---\n")

            elif qtype == "folder":
                folder = fq.get("path", "")
                full_dir = ws_path / folder
                if full_dir.exists() and full_dir.is_dir():
                    parts.append(f'\n--- Folder: {folder}/ (from "{node_label}"{git_ref}) ---\n')
                    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv"}
                    for f in sorted(full_dir.rglob("*")):
                        if not f.is_file():
                            continue
                        if any(sd in f.parts for sd in skip_dirs):
                            continue
                        rel = str(f.relative_to(ws_path))
                        try:
                            content = f.read_text(errors="replace")
                            if len(content) > MAX_FILE_SIZE:
                                content = content[:MAX_FILE_SIZE] + "\n... [truncated]"
                            if total + len(content) > MAX_TOTAL:
                                parts.append(f"\n## {rel} [skipped: size limit]\n")
                                break
                            total += len(content)
                            parts.append(f"\n## {rel}\n{content}\n")
                        except Exception:
                            pass
                    parts.append("---\n")
                elif qnode and qnode.git_commit:
                    # Worktree removed — read from git
                    try:
                        all_files = await list_files_from_commit(qnode.git_commit)
                        folder_prefix = folder.rstrip("/") + "/" if folder else ""
                        skip_dirs = {"node_modules/", "__pycache__/", ".venv/", "venv/"}
                        parts.append(f'\n--- Folder: {folder}/ (from "{node_label}"{git_ref}) ---\n')
                        for rel in all_files:
                            if folder_prefix and not rel.startswith(folder_prefix):
                                continue
                            if any(sd in rel for sd in skip_dirs):
                                continue
                            try:
                                content = await read_file_from_commit(qnode.git_commit, rel)
                                if len(content) > MAX_FILE_SIZE:
                                    content = content[:MAX_FILE_SIZE] + "\n... [truncated]"
                                if total + len(content) > MAX_TOTAL:
                                    parts.append(f"\n## {rel} [skipped: size limit]\n")
                                    break
                                total += len(content)
                                parts.append(f"\n## {rel}\n{content}\n")
                            except Exception:
                                pass
                        parts.append("---\n")
                    except Exception:
                        pass

            elif qtype == "diff":
                content = fq.get("content", "")
                if content:
                    if total + len(content) <= MAX_TOTAL:
                        total += len(content)
                        parts.append(f'\n--- Diff selection (from "{node_label}"{git_ref}) ---\n')
                        parts.append(content)
                        parts.append("\n---\n")

        parts.append(
            "\nUse this quoted context to inform your response. "
            "The user's message follows.]\n\n"
        )
        return "".join(parts)

    # ── Chat preparation ──────────────────────────────────────────────

    async def prepare_chat(
        self,
        parent_node_id: str,
        message: str,
        after_id: str | None = None,
        created_by: str = "human",
        file_quotes: list[dict] | None = None,
        draft_node_id: str | None = None,
    ) -> ChatContext:
        """Create a child node and resolve everything needed to stream a chat.

        If draft_node_id is provided, reuses the existing draft node (created by
        prepare_draft) instead of creating a new child.  The draft's workspace
        is already set up and may contain uploaded files.

        Does NOT start the stream — the caller wires up streaming + transport.
        """
        parent = await get_node(parent_node_id)
        if not parent:
            raise ValueError(f"Parent node {parent_node_id} not found")

        tree = await get_tree(parent.tree_id)
        if not tree:
            raise ValueError(f"Tree for node {parent_node_id} not found")

        # Reuse draft or create new child
        if draft_node_id:
            draft = await get_node(draft_node_id)
            if draft and draft.status == "draft" and draft.parent_id == parent_node_id:
                nid = draft.id
            else:
                log.warning("Draft %s invalid, creating new child", draft_node_id)
                child = await create_child_node(parent_node_id, label=message[:40], created_by=created_by)
                nid = child.id
        else:
            child = await create_child_node(parent_node_id, label=message[:40], created_by=created_by)
            nid = child.id

        # Save user message, set label and status
        label = message[:40]
        update_kwargs: dict = dict(user_message=message, label=label, status="active")
        # Derive quoted_node_ids from file_quotes for DB storage (used for visual arrows)
        # Exclude self-quotes (parent node) — the tree edge already shows the connection
        quoted_node_ids = list(set(fq["node_id"] for fq in file_quotes) - {parent_node_id}) if file_quotes else []
        if quoted_node_ids:
            update_kwargs["quoted_node_ids"] = quoted_node_ids
        await update_node(nid, **update_kwargs)

        # Resolve workspace and ensure worktree
        workspace = resolve_workspace(tree.root_node_id, nid)
        parent_node = await get_node(parent_node_id)
        await ensure_worktree(
            tree.root_node_id, nid,
            parent_node_id,
            parent_node.git_commit if parent_node else None,
        )

        # Resolve parent's session_id for SDK session forking
        parent_session_id = None
        sdk_msg = message
        if parent_node and parent_node.session_id:
            parent_session_id = parent_node.session_id
            parent_ws = resolve_workspace(tree.root_node_id, parent_node.id)
            copy_session(parent_ws, workspace, parent_session_id)

            # Tell the model its workspace has changed
            sdk_msg = (
                f"[System: This conversation was forked into a new git worktree. "
                f"Your working directory is now: {workspace}\n"
                f"All file paths from your previous conversation history refer to a different directory "
                f"that no longer exists. Do NOT reuse any file paths from earlier messages.\n"
                f"Use ONLY your current working directory for all file operations. "
                f"When in doubt, run `pwd` to confirm your location.]\n\n"
                + sdk_msg
            )

        # If parent was cancelled, prepend context (stacks on top of workspace notice)
        if parent_node and parent_node.status == "error" and "[Cancelled by user" in (parent_node.assistant_response or ""):
            partial = parent_node.assistant_response or ""
            sdk_msg = (
                "[System: Your previous response was cancelled by the user. "
                "The session was interrupted mid-execution. Here is your "
                "partial response up to the point of cancellation:\n\n"
                f"{partial}\n\n"
                "Resume from this context. The user's new message follows.]\n\n"
                + sdk_msg
            )

        # Prepend tree-level skill/system prompt if set
        if tree.skill:
            sdk_msg = (
                f"[System: The user has set the following skill/instructions for this entire tree:\n"
                f"{tree.skill}\n"
                f"Follow these instructions for all responses.]\n\n"
                + sdk_msg
            )

        # If file quotes, prepend their context (actual file contents, folder listings, diff)
        if file_quotes:
            quote_ctx = await self._build_file_quote_context(file_quotes, tree, tree.root_node_id)
            sdk_msg = quote_ctx + sdk_msg

        # Resolve effective settings
        effective = await resolve_tree_settings(tree)
        global_cfg = await get_global_defaults()

        child = await get_node(nid)

        return ChatContext(
            node_id=nid,
            node=child,
            workspace=workspace,
            sdk_message=sdk_msg,
            parent_session_id=parent_session_id,
            model=effective["model"],
            max_turns=effective["max_turns"],
            auth_mode=global_cfg["auth_mode"],
            api_key=global_cfg["api_key"],
            after_id=after_id,
        )

    # ── Chat completion / cancel / fail ───────────────────────────────

    async def complete_chat(
        self,
        node_id: str,
        full_response: str,
        user_message: str,
        workspace: Path,
        provider: str | None = None,
        model: str | None = None,
    ) -> ChatResult:
        """Finalise a successful chat: save response, auto-commit, return result."""
        update_kw: dict = dict(assistant_response=full_response, status="done")
        if provider:
            update_kw["provider"] = provider
        if model:
            update_kw["model"] = model
        await update_node(node_id, **update_kw)

        git_commit = None
        files_changed = 0
        try:
            commit_sha, files_changed = await auto_commit(workspace, user_message)
            await update_node(node_id, git_commit=commit_sha)
            git_commit = commit_sha
        except Exception as e:
            log.warning("Auto-commit failed: %s", e)

        # Persist _artifacts/ to durable storage (survives worktree removal)
        node = await get_node(node_id)
        if node:
            persist_artifacts(workspace, node_id)

        # Worktree cleanup is deferred to the caller, which checks for
        # running processes before removing the worktree directory.
        return ChatResult(
            node_id=node_id, full_response=full_response,
            git_commit=git_commit, files_changed=files_changed,
        )

    async def cancel_chat(
        self,
        node_id: str,
        partial_text: str,
        active_tools: list[str],
        workspace: Path | None = None,
    ) -> CancelResult:
        """Save a cancelled chat with a cancellation marker, auto-commit partial changes."""
        cancel_note = "\n\n---\n*[Cancelled by user]*"
        if active_tools:
            cancel_note = (
                "\n\n---\n*[Cancelled by user while running: "
                + ", ".join(active_tools) + "]*"
            )
        full = partial_text + cancel_note
        update_kwargs: dict = dict(status="error", assistant_response=full)

        # Auto-commit partial changes so the worktree can be safely removed
        if workspace and workspace.exists():
            try:
                commit_sha, _ = await auto_commit(workspace, "cancelled mid-stream")
                update_kwargs["git_commit"] = commit_sha
            except Exception as e:
                log.warning("Auto-commit on cancel failed: %s", e)

            # Persist _artifacts/ to durable storage (survives worktree removal)
            node = await get_node(node_id)
            if node:
                persist_artifacts(workspace, node_id)

        await update_node(node_id, **update_kwargs)
        return CancelResult(node_id=node_id, saved_text=cancel_note, active_tools=active_tools)

    async def fail_chat(self, node_id: str) -> None:
        """Mark a chat node as failed."""
        await update_node(node_id, status="error")

    # ── Delete node (subtree) ────────────────────────────────────────

    async def delete_node(self, node_id: str) -> DeleteNodeResult:
        """Delete a node and its subtree.

        Checks for active streams, kills processes, removes worktrees/branches,
        cleans up expanded_nodes and collapsed_subtrees settings.

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

        # Clean up expanded_nodes and collapsed_subtrees settings
        deleted_set = set(deleted_ids)
        raw_exp = await get_setting("expanded_nodes")
        if raw_exp:
            exp_map = json.loads(raw_exp)
            cleaned = {k: v for k, v in exp_map.items() if k not in deleted_set}
            if len(cleaned) != len(exp_map):
                await set_setting("expanded_nodes", json.dumps(cleaned))
        raw_cs = await get_setting("collapsed_subtrees")
        if raw_cs:
            cs_map = json.loads(raw_cs)
            cleaned = {k: v for k, v in cs_map.items() if k not in deleted_set}
            if len(cleaned) != len(cs_map):
                await set_setting("collapsed_subtrees", json.dumps(cleaned))

        return DeleteNodeResult(deleted_ids=deleted_ids, updated_nodes=updated_nodes)

    # ── Update base ──────────────────────────────────────────────────

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
        staleness = await check_staleness(target_branch, resolved_sha)

        updated = await get_tree(tree_id)
        return UpdateBaseResult(
            tree=updated,
            staleness=staleness,
            branches=extra_branches,
        )

    # ── Settings ─────────────────────────────────────────────────────

    async def update_global_settings(self, data: dict) -> dict:
        """Update global settings. Returns updated global defaults dict."""
        for key in ("default_provider", "default_model", "default_max_turns", "auth_mode", "api_key", "summary_model"):
            if key in data:
                val = data[key]
                await set_setting(key, str(val) if val is not None and val != "" else None)
        # data_dir is saved to config file (requires restart)
        if "data_dir" in data and data["data_dir"]:
            from config import save_config
            save_config({"data_dir": data["data_dir"]})
        return await get_global_defaults()

    async def update_tree_settings(self, tree_id: str, data: dict) -> Tree | None:
        """Update tree-level settings. Returns updated tree."""
        updates = {}
        if "provider" in data:
            updates["provider"] = data["provider"] or ""
        if "model" in data:
            updates["model"] = data["model"] or ""
        if "max_turns" in data:
            updates["max_turns"] = data["max_turns"]
        if "skill" in data:
            updates["skill"] = data["skill"] or ""
        if "notes" in data:
            updates["notes"] = data["notes"]
        if updates:
            await update_tree(tree_id, **updates)
        return await get_tree(tree_id)

    # ── File operations ──────────────────────────────────────────────

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

    # ── Merge ────────────────────────────────────────────────────────

    async def merge_to_branch(self, node_id: str, target_branch: str) -> dict:
        """Squash merge a node's branch into target_branch.

        Returns merge result dict.
        """
        node = await get_node(node_id)
        if not node or not node.git_branch:
            return {"ok": False, "error": "Node has no branch"}

        tree = await get_tree(node.tree_id)
        if not tree or not tree.root_node_id:
            return {"ok": False, "error": "Tree not found"}

        # Ensure the worktree/branch exists
        await ensure_worktree(
            tree.root_node_id, node_id,
            node.parent_id, node.git_commit,
        )

        result = await ws_merge_to_branch(node.git_branch, target_branch)
        return result


# ── Stream state (used by active_streams registry) ───────────────────


@dataclass
class StreamState:
    """Tracks state of an active chat stream."""
    node_id: str
    tree_id: str = ""
    text: str = ""
    status: str = "active"   # active | done | error
    send_fn: object = None   # async send callable (tracks current handler)
    sdk_pid: int | None = None
    stream_task: asyncio.Task | None = None
    cancelled: bool = False
