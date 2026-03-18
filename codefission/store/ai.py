"""AI bridge — spawns coding AI tools (Claude, Codex) via agentbridge.

stream_chat() builds a SessionConfig and streams events from the AI subprocess.
resolve_session_continuity() decides fork (same provider) vs context transfer
(different provider). Builds the system prompt injected into every AI call.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncGenerator

from agentbridge import (
    BridgeEvent,
    SessionConfig,
    ProviderType,
    TextDelta,
    ToolStart,
    ToolEnd,
    SessionInit,
    TurnComplete,
    create_session,
)
from store.trees import get_tree, get_node, get_ancestor_chain

log = logging.getLogger(__name__)


# ── Session continuity ───────────────────────────────────────────────


def _build_context_from_ancestors(ancestors: list) -> str:
    """Walk ancestor chain, collect conversations, format as text preamble.

    Used when switching providers mid-tree — the new provider gets
    a text summary of prior conversation rather than a native session fork.
    """
    parts = ["[System: Previous conversation history from a different AI provider:\n"]
    for node in ancestors:
        if node.user_message:
            parts.append(f"\nUser: {node.user_message}\n")
        if node.assistant_response:
            response = node.assistant_response
            if len(response) > 10_000:
                response = response[:10_000] + "\n... [truncated]"
            parts.append(f"\nAssistant: {response}\n")
    parts.append("\nEnd of previous context. The new conversation continues below.]\n\n")
    return "".join(parts)


async def resolve_session_continuity(
    parent_node,
    new_provider: str,
    ancestors: list | None = None,
) -> tuple:
    """Decide: fork parent's session, or build context transfer text.

    Returns (resume_session_id, fork_session, prior_context).
    """
    if not parent_node or not parent_node.user_message:
        return None, False, None

    parent_provider = parent_node.provider or "claude-code"

    if parent_node.session_id and parent_provider == new_provider:
        return parent_node.session_id, True, None
    else:
        if ancestors is None:
            ancestors = await get_ancestor_chain(parent_node.id)
        prior_context = _build_context_from_ancestors(ancestors)
        return None, False, prior_context


# ── Helpers ──────────────────────────────────────────────────────────


def _sdk_env(api_key: str = "", provider: str = "claude-code") -> dict[str, str]:
    """Build env dict for the provider subprocess.

    Sets the correct API key env var based on provider.
    """
    env: dict[str, str] = {}
    if api_key:
        if provider == "codex":
            env["OPENAI_API_KEY"] = api_key
        else:
            env["ANTHROPIC_API_KEY"] = api_key
    return env


# ── System prompt (STATIC — same for every call in a tree) ───────────


def _build_system_prompt(tree=None, tree_instructions: str = "") -> str:
    """Build the system prompt. This is STATIC per tree — no node-specific data.

    Dynamic context (workspace path, git branch, commit) goes in the user
    message to maximize prompt cache hits across turns. The system prompt
    is the cache key prefix — keeping it stable means Claude can reuse
    cached input tokens.
    """
    parts = [
        "You are a helpful AI coding assistant in CodeFission, a tree-structured "
        "development tool where each node is an isolated git worktree. "
        "Be concise and helpful."
    ]

    if tree:
        parts.append(
            "\n\n### STRICT FILESYSTEM RULES"
            "\n- ONLY write, create, modify, or delete files inside your working directory."
            "\n- NEVER write to the user's home directory, other projects, /etc, or any path outside your workspace."
            "\n- NEVER run `rm`, `mv`, `cp`, `touch`, `tee`, `>`, or any write operation on paths outside your workspace."
            "\n- You may READ files anywhere on the system for reference."
            "\n- If a task requires writing outside your workspace, explain what's needed and let the user do it."
        )

        parts.append(
            "\n\nAll your file changes are automatically committed after each response. "
            "Focus on writing code and making changes — git operations are handled for you."
            "\n\nFILE PERSISTENCE: Your worktree is ephemeral — it is deleted after your "
            "response completes. Code and project files are auto-committed to git and survive. "
            "Generated output files (plots, images, CSVs, etc.) should be saved to "
            "`_artifacts/` (e.g., `_artifacts/plot.png`). The `_artifacts/` directory is "
            "gitignored but persisted separately."
            "\n\nWhen referencing generated files in your response, use the `_artifacts/` path: "
            "`![Plot](_artifacts/plot.png)` or `[Download results](_artifacts/results.csv)`."
            "\nNEVER save files to `tmp/`, `/tmp/`, or any other temporary directory."
            "\n\nIMPORTANT: Other branches in this repo belong to sibling conversation nodes "
            "and are completely independent. Do NOT use `git log --all`, `git branch`, "
            "`git show` on other branches, or reference any branch other than your own."
        )

        parts.append(
            "\n\n## Response Format"
            "\nYour response is rendered Markdown. Always lead with the most "
            "visual, tangible result first — text explanation comes last."
            "\n"
            "\n**Show, don't tell.** Prefer rich output over plain text:"
            "\n- Images/plots: embed inline with `![description](_artifacts/file.png)`."
            "\n- Videos/animations: embed with `![description](_artifacts/file.mp4)` "
            "(rendered as a playable `<video>`)."
            "\n- Websites/UIs: launch the app, take a screenshot, embed it inline, "
            "and display the URL. When asked to build a webapp, always start the "
            "dev server, capture a screenshot of the running page, and include both "
            "the screenshot and the URL in your response."
            "\n- Data results: show a table or chart before describing findings."
            "\n- Code changes: show key snippets or a summary — never dump entire files."
            "\n"
            "\nSupported media in Markdown image syntax: `.png`, `.jpg`, `.gif`, `.svg`, "
            "`.mp4`, `.webm`, `.mov`. Save all generated output to `_artifacts/`."
        )

    if tree_instructions:
        parts.append(
            "\n\n## Tree Instructions\n"
            "The user has set the following instructions for this entire tree. "
            "Follow them for all responses:\n\n"
            + tree_instructions
        )

    return "".join(parts)


# ── Dynamic context (per-node, goes in user message) ─────────────────


def _build_workspace_context(workspace: Path, node, tree) -> str:
    """Build the dynamic workspace context that goes at the start of the user message.

    This changes per node (workspace path, branch, commit) so it must NOT
    be in the system prompt — that would break cache hits.
    """
    from config import get_project_path

    parts = []
    parts.append(f"[Workspace: {workspace}")

    if tree:
        base_branch = tree.base_branch or "main"
        parts.append(f" | Project: {get_project_path()}")
        parts.append(f" | Branch: {node.git_branch or 'unknown'}")
        if node.git_commit:
            parts.append(f" | Commit: {node.git_commit[:12]}")
        parts.append(f" | Base: {base_branch}")

    is_root = not node.parent_id
    if is_root:
        parts.append(" | Root node (main branch)")
    else:
        parts.append(" | Branch node (isolated worktree)")

    parts.append("]\n\n")
    return "".join(parts)


# ── Provider type mapping ────────────────────────────────────────────

_PROVIDER_TYPE_MAP = {
    "claude-code": ProviderType.CLAUDE,
    "codex": ProviderType.CODEX,
}


# ── Main streaming function ──────────────────────────────────────────


async def stream_chat(
    node_id: str,
    user_message: str,
    workspace: Path,
    parent_session_id: str | None = None,
    *,
    provider: str = "claude-code",
    model: str = "claude-opus-4-6",
    api_key: str = "",
    tree_instructions: str = "",
) -> AsyncGenerator[BridgeEvent, None]:
    """Stream a chat response for a node via AgentBridge."""
    node = await get_node(node_id)
    if not node:
        return

    tree = await get_tree(node.tree_id)
    if not tree:
        return

    provider_type = _PROVIDER_TYPE_MAP.get(provider, ProviderType.CLAUDE)

    # STATIC system prompt — same for every call in this tree (maximizes cache hits)
    system_prompt = _build_system_prompt(tree=tree, tree_instructions=tree_instructions)

    # DYNAMIC workspace context — prepended to user message (changes per node)
    workspace_ctx = _build_workspace_context(workspace, node, tree)

    # Resolve session continuity (fork vs context transfer)
    resume_sid = None
    fork = False
    prior_context = None

    if parent_session_id:
        parent_node = await get_node(node.parent_id) if node.parent_id else None
        if parent_node:
            resume_sid, fork, prior_context = await resolve_session_continuity(
                parent_node, provider
            )
        else:
            resume_sid = None
            fork = False

    # Verify session file exists for fork
    if resume_sid and fork:
        from store.git import session_file_exists
        if not session_file_exists(workspace, resume_sid):
            log.info("Parent session file missing, starting fresh for node %s", node_id)
            resume_sid = None
            fork = False

    # Build the prompt:
    # - Workspace context (always first — tells the AI where it's working)
    # - Cross-provider context transfer (if switching providers)
    # - The actual user message
    prompt = workspace_ctx
    if prior_context:
        prompt += prior_context
    prompt += user_message

    # Build the config
    config_kwargs = dict(
        provider=provider_type,
        prompt=prompt,
        cwd=workspace,
        model=model,
        env=_sdk_env(api_key, provider),
        permission_mode="bypassPermissions",
        disable_global_memory=True,  # skip user-level memory, keep project CLAUDE.md/AGENTS.md
    )

    if resume_sid:
        config_kwargs["resume_session_id"] = resume_sid
        config_kwargs["fork_session"] = fork
        # For Claude forks: don't resend system_prompt (already in session history).
        # For Codex: resume barely works, but if it does, system_prompt should be included
        # since Codex prepends it to the prompt (no separate system channel).
        if provider_type != ProviderType.CLAUDE:
            config_kwargs["system_prompt"] = system_prompt
    else:
        config_kwargs["system_prompt"] = system_prompt

    config = SessionConfig(**config_kwargs)

    async for event in create_session(config):
        yield event


# ── Demo ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    """Standalone demo — streams a prompt through AgentBridge."""
    import asyncio
    import sys

    async def main():
        prompt = sys.argv[1] if len(sys.argv) > 1 else "Say hello in one sentence."
        model = "claude-sonnet-4-6"
        if len(sys.argv) > 2 and sys.argv[2].startswith("--model"):
            model = sys.argv[2].split("=")[1] if "=" in sys.argv[2] else sys.argv[3]

        from pathlib import Path
        config = SessionConfig(
            provider=ProviderType.CLAUDE,
            prompt=prompt,
            cwd=Path.cwd(),
            model=model,
            permission_mode="bypassPermissions",
        )

        async for event in create_session(config):
            if isinstance(event, TextDelta):
                print(event.text, end="", flush=True)
            elif isinstance(event, ToolStart):
                print(f"\n[tool: {event.name}]")
            elif isinstance(event, ToolEnd):
                print(f"[result: {event.result[:100] if event.result else ''}]")
            elif isinstance(event, TurnComplete):
                print(f"\n[done — cost: ${event.cost_usd:.4f}]" if event.cost_usd else "\n[done]")

    asyncio.run(main())
