"""Transport-agnostic orchestrator — business logic for tree/node/chat operations.

Both the WebSocket handler and future headless agents (shadow, CI) call into
this class instead of scattering logic across the transport layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from models import Node, Tree
from services.tree_service import (
    create_tree as _create_tree,
    get_tree,
    get_node,
    create_child_node,
    update_node,
    update_tree,
    get_global_defaults,
    resolve_tree_settings,
)
from services.workspace_service import (
    setup_repo,
    create_worktree,
    ensure_worktree,
    auto_commit,
    resolve_workspace,
    copy_session,
    _run_git,
    WORKSPACES_DIR,
    read_file_from_commit,
    list_files_from_commit,
)

log = logging.getLogger(__name__)


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
    sandbox: bool = False
    quoted_node_ids: list[str] = field(default_factory=list)


@dataclass
class ChatResult:
    """Returned after a successful chat completion."""
    node_id: str
    full_response: str
    git_commit: str | None = None


@dataclass
class CancelResult:
    """Returned after a cancelled chat."""
    node_id: str
    saved_text: str
    active_tools: list[str] = field(default_factory=list)


# ── Orchestrator ──────────────────────────────────────────────────────


class Orchestrator:
    """Stateless business-logic coordinator.

    Methods perform the multi-step workflows that were previously inlined
    in ConnectionHandler. They return data; the caller decides how to
    deliver it (WebSocket, stdout, etc.).
    """

    async def create_tree(
        self,
        name: str,
        repo_mode: str = "new",
        repo_source: str | None = None,
    ) -> tuple[Tree, Node]:
        """Create a tree + root node + git repo. Returns (tree, root_node)."""
        tree, root = await _create_tree(name, repo_mode=repo_mode)
        await setup_repo(tree.id, root.id, repo_mode, repo_source)

        root_dir = WORKSPACES_DIR / tree.id / root.id
        _, head_sha, _ = await _run_git(root_dir, "rev-parse", "HEAD")
        _, branch, _ = await _run_git(root_dir, "rev-parse", "--abbrev-ref", "HEAD")
        await update_node(root.id, git_branch=branch, git_commit=head_sha)

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

    async def set_repo(
        self,
        tree_id: str,
        repo_mode: str,
        repo_source: str | None = None,
    ) -> tuple[Tree, Node]:
        """Configure (or reconfigure) the repo for a tree. Returns updated (tree, root_node)."""
        tree = await get_tree(tree_id)
        if not tree or not tree.root_node_id:
            raise ValueError("Tree not found")

        root_dir = WORKSPACES_DIR / tree.id / tree.root_node_id
        if root_dir.exists() and repo_mode != tree.repo_mode:
            import shutil
            shutil.rmtree(root_dir, ignore_errors=True)

        await setup_repo(tree.id, tree.root_node_id, repo_mode, repo_source)
        root_dir = WORKSPACES_DIR / tree.id / tree.root_node_id
        _, head_sha, _ = await _run_git(root_dir, "rev-parse", "HEAD")
        _, branch, _ = await _run_git(root_dir, "rev-parse", "--abbrev-ref", "HEAD")
        await update_node(tree.root_node_id, git_branch=branch, git_commit=head_sha)
        await update_tree(tree.id, repo_mode=repo_mode, repo_source=repo_source)

        updated_tree = await get_tree(tree.id)
        root_node = await get_node(tree.root_node_id)
        return updated_tree, root_node

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
            ws_path = resolve_workspace(tree.id, root_node_id, nid)
            node_label = qnode.label if qnode else nid[:8]

            # Build git ref metadata for the quoted node
            git_ref = ""
            if qnode and qnode.git_commit:
                branch = qnode.git_branch or f"ct-{nid}"
                git_ref = f", branch: {branch}, commit: {qnode.git_commit[:12]}"

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
                            content = await read_file_from_commit(tree.id, root_node_id, qnode.git_commit, fpath)
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
                        all_files = await list_files_from_commit(tree.id, root_node_id, qnode.git_commit)
                        folder_prefix = folder.rstrip("/") + "/" if folder else ""
                        skip_dirs = {"node_modules/", "__pycache__/", ".venv/", "venv/"}
                        parts.append(f'\n--- Folder: {folder}/ (from "{node_label}"{git_ref}) ---\n')
                        for rel in all_files:
                            if folder_prefix and not rel.startswith(folder_prefix):
                                continue
                            if any(sd in rel for sd in skip_dirs):
                                continue
                            try:
                                content = await read_file_from_commit(tree.id, root_node_id, qnode.git_commit, rel)
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

    async def prepare_chat(
        self,
        parent_node_id: str,
        message: str,
        after_id: str | None = None,
        created_by: str = "human",
        file_quotes: list[dict] | None = None,
    ) -> ChatContext:
        """Create a child node and resolve everything needed to stream a chat.

        Does NOT start the stream — the caller wires up streaming + transport.
        """
        parent = await get_node(parent_node_id)
        if not parent:
            raise ValueError(f"Parent node {parent_node_id} not found")

        tree = await get_tree(parent.tree_id)
        if not tree:
            raise ValueError(f"Tree for node {parent_node_id} not found")

        # Create child node
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
        workspace = resolve_workspace(tree.id, tree.root_node_id, nid)
        parent_node = await get_node(parent_node_id)
        await ensure_worktree(
            tree.id, tree.root_node_id, nid,
            parent_node_id,
            parent_node.git_commit if parent_node else None,
        )

        # Resolve parent's session_id for SDK session forking
        parent_session_id = None
        sdk_msg = message
        if parent_node and parent_node.session_id:
            parent_session_id = parent_node.session_id
            parent_ws = resolve_workspace(tree.id, tree.root_node_id, parent_node.id)
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

        # If file quotes, prepend their context (actual file contents, folder listings, diff)
        if file_quotes:
            quote_ctx = await self._build_file_quote_context(file_quotes, tree, tree.root_node_id)
            sdk_msg = quote_ctx + sdk_msg

        # Resolve effective settings
        effective = await resolve_tree_settings(tree)
        global_cfg = await get_global_defaults()

        child = await get_node(nid)

        # Sandbox is opt-in via global setting
        from services.tree_service import get_setting
        sandbox_enabled = (await get_setting("sandbox")) == "true"

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
            sandbox=sandbox_enabled,
        )

    async def complete_chat(
        self,
        node_id: str,
        full_response: str,
        user_message: str,
        workspace: Path,
    ) -> ChatResult:
        """Finalise a successful chat: save response, auto-commit, return result."""
        await update_node(node_id, assistant_response=full_response, status="done")

        git_commit = None
        files_changed = 0
        try:
            commit_sha, files_changed = await auto_commit(workspace, user_message)
            await update_node(node_id, git_commit=commit_sha)
            git_commit = commit_sha
        except Exception as e:
            log.warning("Auto-commit failed: %s", e)

        # If no files changed, remove worktree + branch (they're just noise)
        if files_changed == 0:
            try:
                node = await get_node(node_id)
                if node and node.parent_id:
                    tree = await get_tree(node.tree_id)
                    if tree:
                        from services.workspace_service import remove_worktree_and_branch
                        await remove_worktree_and_branch(tree.id, tree.root_node_id, node_id)
                        # Clear git_branch so it's not shown as having its own branch
                        await update_node(node_id, git_branch=None)
            except Exception as e:
                log.debug("No-change worktree cleanup failed: %s", e)

        return ChatResult(node_id=node_id, full_response=full_response, git_commit=git_commit)

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

        await update_node(node_id, **update_kwargs)
        return CancelResult(node_id=node_id, saved_text=cancel_note, active_tools=active_tools)

    async def fail_chat(self, node_id: str) -> None:
        """Mark a chat node as failed."""
        await update_node(node_id, status="error")
