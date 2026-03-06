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
    continue_conversation: bool = False


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
        """Create a child node with its own git worktree."""
        node = await create_child_node(parent_id, label, created_by=created_by)

        parent = await get_node(parent_id)
        if parent:
            tree = await get_tree(parent.tree_id)
            if tree:
                try:
                    await create_worktree(
                        tree.id, tree.root_node_id, node.id,
                        parent.git_commit or "HEAD",
                    )
                    branch_name = f"ct-{node.id}"
                    await update_node(node.id, git_branch=branch_name)
                    node = await get_node(node.id)
                except Exception as e:
                    log.warning("Worktree creation failed: %s", e)

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

    async def prepare_chat(
        self,
        parent_node_id: str,
        message: str,
        after_id: str | None = None,
        created_by: str = "human",
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
        await update_node(nid, user_message=message, label=label, status="active")

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
                f"[System: The user has run a `git worktree` command. "
                f"You are now in a DIFFERENT working directory: {workspace}\n"
                f"All file paths from your previous conversation history refer to a different directory "
                f"and are NO LONGER VALID. Do NOT copy or reuse any file paths from earlier messages.\n"
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
        try:
            commit_sha, _ = await auto_commit(workspace, user_message)
            await update_node(node_id, git_commit=commit_sha)
            git_commit = commit_sha
        except Exception as e:
            log.warning("Auto-commit failed: %s", e)

        return ChatResult(node_id=node_id, full_response=full_response, git_commit=git_commit)

    async def cancel_chat(
        self,
        node_id: str,
        partial_text: str,
        active_tools: list[str],
    ) -> CancelResult:
        """Save a cancelled chat with a cancellation marker."""
        cancel_note = "\n\n---\n*[Cancelled by user]*"
        if active_tools:
            cancel_note = (
                "\n\n---\n*[Cancelled by user while running: "
                + ", ".join(active_tools) + "]*"
            )
        full = partial_text + cancel_note
        await update_node(node_id, status="error", assistant_response=full)
        return CancelResult(node_id=node_id, saved_text=cancel_note, active_tools=active_tools)

    async def prepare_continue_chat(
        self,
        node_id: str,
        message: str,
    ) -> ChatContext:
        """Prepare to continue an existing conversation on the same node.

        Resumes the SDK session instead of forking it. No new node is created.
        """
        node = await get_node(node_id)
        if not node:
            raise ValueError(f"Node {node_id} not found")
        if not node.session_id:
            raise ValueError(f"Node {node_id} has no session to continue")

        tree = await get_tree(node.tree_id)
        if not tree:
            raise ValueError(f"Tree for node {node_id} not found")

        await update_node(node_id, status="active")

        workspace = resolve_workspace(tree.id, tree.root_node_id, node_id)

        effective = await resolve_tree_settings(tree)
        global_cfg = await get_global_defaults()

        from services.tree_service import get_setting
        sandbox_enabled = (await get_setting("sandbox")) == "true"

        node = await get_node(node_id)

        return ChatContext(
            node_id=node_id,
            node=node,
            workspace=workspace,
            sdk_message=message,
            parent_session_id=node.session_id,
            model=effective["model"],
            max_turns=effective["max_turns"],
            auth_mode=global_cfg["auth_mode"],
            api_key=global_cfg["api_key"],
            sandbox=sandbox_enabled,
            continue_conversation=True,
        )

    async def complete_continue_chat(
        self,
        node_id: str,
        new_response: str,
        user_message: str,
        workspace: Path,
    ) -> ChatResult:
        """Finalise a continued chat: append response, auto-commit."""
        node = await get_node(node_id)
        if not node:
            raise ValueError(f"Node {node_id} not found")

        # Append the follow-up exchange to the existing response
        separator = f"\n\n---\n\n**You:** {user_message}\n\n"
        full = node.assistant_response + separator + new_response
        await update_node(node_id, assistant_response=full, status="done")

        git_commit = None
        try:
            commit_sha, _ = await auto_commit(workspace, user_message)
            await update_node(node_id, git_commit=commit_sha)
            git_commit = commit_sha
        except Exception as e:
            log.warning("Auto-commit failed: %s", e)

        return ChatResult(node_id=node_id, full_response=full, git_commit=git_commit)

    async def fail_chat(self, node_id: str) -> None:
        """Mark a chat node as failed."""
        await update_node(node_id, status="error")
