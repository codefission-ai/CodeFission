"""Chat lifecycle — prepare, stream, complete, cancel.

prepare_chat: creates a child node, resolves workspace, builds the AI prompt
  (including file quotes, session forking context, cancelled-parent context).
chat: async generator that streams AI events (TextDelta, ToolStart, etc.)
  and handles PID tracking, timeout, cancellation, worktree cleanup.
complete_chat: saves the AI response, auto-commits file changes.
cancel_chat: saves partial response with cancellation marker.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import AsyncGenerator

from models import Node, Tree
from store.trees import (
    get_tree,
    get_node,
    create_child_node,
    update_node,
    update_tree,
    get_drafts_for_parent,
    delete_single_node,
)
from store.settings import (
    resolve_tree_settings,
    get_global_defaults,
    get_effective_api_key,
)
from store.git import (
    ensure_worktree,
    auto_commit,
    persist_artifacts,
    resolve_workspace,
    copy_session,
    remove_worktree_and_branch,
    read_file_from_commit,
    list_files_from_commit,
)
from store.ai import stream_chat, TextDelta, ToolStart, ToolEnd, SessionInit, TurnComplete
from models import (
    ChatNodeCreated,
    ChatCompleted,
    ChatContext,
    ChatResult,
    CancelResult,
)

log = logging.getLogger(__name__)


class ChatMixin:
    """Chat lifecycle methods for the Orchestrator."""

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

    async def _create_chat_node(
        self,
        parent_node_id: str,
        message: str,
        after_id: str | None = None,
        created_by: str = "human",
        file_quotes: list[dict] | None = None,
        draft_node_id: str | None = None,
    ) -> tuple[Node, str | None]:
        """Fast path: create the child node in DB and return it immediately.

        This lets the UI show the node before the slow git worktree setup.
        """
        parent = await get_node(parent_node_id)
        if not parent:
            raise ValueError(f"Parent node {parent_node_id} not found")

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
        quoted_node_ids = list(set(fq["node_id"] for fq in file_quotes) - {parent_node_id}) if file_quotes else []
        if quoted_node_ids:
            update_kwargs["quoted_node_ids"] = quoted_node_ids
        await update_node(nid, **update_kwargs)

        node = await get_node(nid)
        return node, after_id

    async def _finish_prepare_chat(
        self,
        node_id: str,
        parent_node_id: str,
        message: str,
        after_id: str | None = None,
        created_by: str = "human",
        file_quotes: list[dict] | None = None,
    ) -> ChatContext:
        """Slow path: set up git worktree, resolve session/settings."""
        parent_node = await get_node(parent_node_id)
        tree = await get_tree(parent_node.tree_id) if parent_node else None
        if not tree:
            raise ValueError(f"Tree for node {parent_node_id} not found")

        nid = node_id
        workspace = resolve_workspace(tree.root_node_id, nid)
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

        # File quotes
        if file_quotes:
            quote_ctx = await self._build_file_quote_context(file_quotes, tree, tree.root_node_id)
            sdk_msg = quote_ctx + sdk_msg

        # Resolve effective settings
        effective = await resolve_tree_settings(tree)

        child = await get_node(nid)

        return ChatContext(
            node_id=nid,
            node=child,
            workspace=workspace,
            sdk_message=sdk_msg,
            parent_session_id=parent_session_id,
            provider=effective["provider"],
            model=effective["model"],
            api_key=await get_effective_api_key(effective["provider"]),
            after_id=after_id,
            tree_instructions=tree.skill or "",
        )

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

        # File quotes: prepend actual file contents to user message
        # (This is user-initiated context — goes in the message, not system prompt)
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
            provider=effective["provider"],
            model=effective["model"],
            api_key=await get_effective_api_key(effective["provider"]),
            after_id=after_id,
            tree_instructions=tree.skill or "",
        )

    # ── Chat async generator ────────────────────────────────────────

    async def chat(
        self,
        parent_node_id: str,
        message: str,
        after_id: str | None = None,
        created_by: str = "human",
        file_quotes: list[dict] | None = None,
        draft_node_id: str | None = None,
    ) -> AsyncGenerator:
        """Run a chat as an async generator yielding domain events.

        Yields in order:
          1. ChatNodeCreated  — a new child node was created
          2. SessionInit / TextDelta / ToolStart / ToolEnd  — stream events
          3. ChatCompleted  — chat finished successfully

        On error, the generator marks the node as failed and raises.
        The caller (WS presenter, REST SSE, CLI) consumes events and
        delivers them over its transport.
        """
        t_chat_start = time.monotonic()

        # 1a. Create child node in DB (fast) and notify UI immediately
        early_node, early_after_id = await self._create_chat_node(
            parent_node_id, message,
            after_id=after_id,
            created_by=created_by,
            file_quotes=file_quotes,
            draft_node_id=draft_node_id,
        )
        log.info("[chat] node created in %.1fs: %s", time.monotonic() - t_chat_start, early_node.id[:8])
        yield ChatNodeCreated(node=early_node, after_id=early_after_id)

        # 1b. Prepare workspace, worktree, session (slow — git worktree add)
        t_prep = time.monotonic()
        ctx = await self._finish_prepare_chat(
            early_node.id, parent_node_id, message,
            after_id=early_after_id,
            created_by=created_by,
            file_quotes=file_quotes,
        )
        nid = ctx.node_id
        log.info("[chat] workspace prepared in %.1fs (worktree + session copy)", time.monotonic() - t_prep)

        # 2. Stream the chat via agentbridge
        full_text = ""
        provider_name = ctx.provider or "claude"
        chunk_count = 0

        try:
            t_stream = time.monotonic()
            async for event in stream_chat(
                nid, ctx.sdk_message, ctx.workspace, ctx.parent_session_id,
                provider=ctx.provider,
                model=ctx.model,
                api_key=ctx.api_key,
                tree_instructions=ctx.tree_instructions,
            ):
                if isinstance(event, SessionInit):
                    log.info("[chat] SessionInit after %.1fs (session=%s)", time.monotonic() - t_stream, event.session_id[:8] if event.session_id else "?")
                    await update_node(nid, session_id=event.session_id)
                    if hasattr(event, "provider") and event.provider:
                        provider_name = event.provider
                    yield event

                elif isinstance(event, TextDelta):
                    chunk_count += 1
                    if chunk_count == 1:
                        log.info("[chat] first TextDelta after %.1fs", time.monotonic() - t_stream)
                    full_text += event.text
                    yield event

                elif isinstance(event, ToolStart):
                    yield event

                elif isinstance(event, ToolEnd):
                    yield event

                elif isinstance(event, TurnComplete):
                    # TurnComplete signals end of streaming — don't yield it,
                    # instead finalize and yield ChatCompleted below
                    pass

            log.info("[chat] stream finished: %d chunks, %d chars, %.1fs total", chunk_count, len(full_text), time.monotonic() - t_chat_start)

            # 3. Complete: save response, auto-commit, yield ChatCompleted
            result = await self.complete_chat(
                nid, full_text, message, ctx.workspace,
                provider=provider_name,
                model=ctx.model,
            )
            yield ChatCompleted(result=result)

        except Exception:
            # Mark node as failed before re-raising
            await self.fail_chat(nid)
            raise

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

    async def auto_name_tree(self, tree_id: str, first_message: str, tree=None) -> str | None:
        """Generate and save a short name for a tree based on its first message.

        Returns the new name, or None if auto-naming failed or was disabled.
        """
        try:
            from store.summary import generate_tree_name

            defaults = await get_global_defaults()
            summary_model = defaults.get("summary_model") or ""
            if not summary_model:
                return None  # auto-naming disabled
            api_key = defaults.get("api_key") or None

            if not tree:
                tree = await get_tree(tree_id)
            if not tree:
                return None

            repo_info = tree.base_branch or "main"
            name = await generate_tree_name(
                skill=tree.skill,
                repo_info=repo_info,
                first_message=first_message,
                model=summary_model,
                api_key=api_key,
            )
            if name:
                await update_tree(tree_id, name=name)
            return name
        except Exception:
            log.warning("Auto-name tree failed for %s", tree_id, exc_info=True)
            return None

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
        await delete_single_node(draft_node_id)
