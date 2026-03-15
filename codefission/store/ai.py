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
    SessionConfig,
    ProviderType,
    create_session,
    BridgeEvent,
    TextDelta,
    ToolStart,
    ToolEnd,
    SessionInit,
    TurnComplete,
)

from store.trees import get_tree, get_node, get_ancestor_chain

log = logging.getLogger(__name__)


# ── Session continuity ───────────────────────────────────────────────

async def resolve_session_continuity(
    parent_node,
    new_provider: str,
    ancestors: list | None = None,
) -> tuple:
    """Decide: fork parent's session, or build context transfer text.

    Returns (resume_session_id, fork_session, prior_context).

    - Same provider: session fork via resume_session_id + fork_session=True
    - Different provider or no session: context transfer via prior_context text
    - Root/empty parent: fresh start (no fork, no context)
    """
    if not parent_node or not parent_node.user_message:
        # Root node or empty parent — fresh start
        return None, False, None

    parent_provider = parent_node.provider or "claude"  # default to claude for old nodes

    if parent_node.session_id and parent_provider == new_provider:
        # Same provider — fork the session (works across models within same provider)
        return parent_node.session_id, True, None
    else:
        # Different provider or no session — context transfer
        if ancestors is None:
            ancestors = await get_ancestor_chain(parent_node.id)
            # Include parent itself in the chain
            ancestors = ancestors + [parent_node]
        prior_context = _build_context_from_ancestors(ancestors)
        return None, False, prior_context


def _build_context_from_ancestors(ancestors: list) -> str:
    """Walk ancestor chain, collect conversations, format as text preamble.

    This is used when switching providers mid-tree — the new provider gets
    a text summary of prior conversation rather than a native session fork.
    """
    parts = ["[System: Previous conversation history from a different AI provider:\n"]
    for node in ancestors:
        if node.user_message:
            parts.append(f"\nUser: {node.user_message}\n")
        if node.assistant_response:
            # Truncate very long responses
            response = node.assistant_response
            if len(response) > 10_000:
                response = response[:10_000] + "\n... [truncated]"
            parts.append(f"\nAssistant: {response}\n")
    parts.append("\nUse this context to inform your response. The user's new message follows.]\n\n")
    return "".join(parts)


# ── Helpers ──────────────────────────────────────────────────────────

def _sdk_env(api_key: str = "", provider: str = "claude-code") -> dict[str, str]:
    """Build env dict for the provider subprocess.

    Sets the correct API key env var based on provider:
    - claude-code → ANTHROPIC_API_KEY
    - codex → OPENAI_API_KEY

    Kept as a public function — summary_service imports it.
    """
    env: dict[str, str] = {}
    if api_key:
        if provider == "codex":
            env["OPENAI_API_KEY"] = api_key
        else:
            env["ANTHROPIC_API_KEY"] = api_key
    return env


def _build_system_prompt(node, tree=None, workspace: Path | None = None) -> str:
    parts = [
        "You are a helpful AI coding assistant in CodeFission, a tree-structured "
        "development tool where each node is an isolated git worktree. "
        "Be concise and helpful."
    ]

    # Repo / workspace context
    if tree and workspace:
        parts.append("\n\n## Workspace")
        parts.append(f"\nYour working directory is: {workspace}")
        parts.append(
            "\n\n### STRICT FILESYSTEM RULES"
            "\n- ONLY write, create, modify, or delete files inside your working directory shown above."
            "\n- NEVER write to the user's home directory, other projects, /etc, or any path outside your workspace."
            "\n- NEVER run `rm`, `mv`, `cp`, `touch`, `tee`, `>`, or any write operation on paths outside your workspace."
            "\n- You may READ files anywhere on the system for reference (e.g., to inspect dependencies or configs)."
            "\n- If a task requires writing outside your workspace, explain what's needed and let the user do it."
        )

        from config import get_project_path
        base_branch = tree.base_branch or "main"
        parts.append(
            f"\nThis is the user's project at {get_project_path()}. "
            f"You are working in a git worktree branched from '{base_branch}'. "
            f"Changes here are isolated until merged back."
        )

        is_root = not node.parent_id
        if is_root:
            parts.append(
                "\nYou are working on the root node (main branch). "
                "Your changes here form the base that child branches evolve from."
            )
        else:
            parts.append(
                "\nYou are working on a branch node (git worktree). "
                "This worktree was forked from the parent node's commit. "
                "Your changes here are isolated and do not affect the parent or sibling branches."
                "\n\nCRITICAL: Your working directory has changed from previous conversations in this session. "
                "ALWAYS use the working directory path shown above. NEVER reuse file paths from earlier "
                "messages — they reference a different worktree that is not your current workspace."
            )
        if node.git_branch:
            parts.append(f"\nGit branch: {node.git_branch}")
        if node.git_commit:
            parts.append(f"\nCurrent commit: {node.git_commit}")

        parts.append(
            "\n\nAll your file changes are automatically committed after each response. "
            "Focus on writing code and making changes — git operations are handled for you."
            "\n\nFILE PERSISTENCE: Your worktree is ephemeral — it is deleted after your "
            "response completes. Code and project files in your working directory are "
            "auto-committed to git and survive. Generated output files (plots, images, "
            "screenshots, data exports, CSVs, etc.) should be saved to the `_artifacts/` "
            "directory (e.g., `_artifacts/plot.png`, `_artifacts/results.csv`). The "
            "`_artifacts/` directory is gitignored but its contents are persisted separately "
            "and remain viewable after the worktree is removed. "
            "NEVER save files to `tmp/`, `/tmp/`, or any other temporary directory."
            "\n\nWhen referencing generated files in your response, use the `_artifacts/` path: "
            "`![Plot](_artifacts/plot.png)` or `[Download results](_artifacts/results.csv)`."
            "\n\nIMPORTANT: Other branches in this repo belong to sibling conversation nodes "
            "and are completely independent. Do NOT use `git log --all`, `git branch`, "
            "`git show` on other branches, or reference any branch other than your own. "
            "Only interact with files in your working directory."
        )

        parts.append(
            "\n\n## Response Format"
            "\nYour response is displayed as rendered Markdown. Use this to surface the "
            "artifacts the user cares about most — don't make them hunt through files:"
            "\n- Experiments/data science: include result tables, metric summaries, and "
            "embed plots as inline images (`![](path/to/plot.png)`)."
            "\n- Web development: show the local URL/port so the user can open it immediately."
            "\n- Media (images, audio, video): embed or link to the generated files inline."
            "\n- Documents that Markdown can't render (PDF, DOCX, slides, spreadsheets): "
            "list the file paths so the user can open or download them."
            "\n- Code changes: show the key snippets or a summary — not the entire file."
            "\nIn short: if you produced something the user will want to see, show it or "
            "link to it directly in your response."
        )

    return "".join(parts)


# ── Main streaming function ──────────────────────────────────────────

_PROVIDER_TYPE_MAP = {
    "claude-code": ProviderType.CLAUDE,
    "codex": ProviderType.CODEX,
}


async def stream_chat(
    node_id: str,
    user_message: str,
    workspace: Path,
    parent_session_id: str | None = None,
    *,
    provider: str = "claude",
    model: str = "claude-opus-4-6",
    api_key: str = "",
    tree_instructions: str = "",
) -> AsyncGenerator[BridgeEvent, None]:
    """Stream a chat response for a node via AgentBridge.

    If parent_session_id is provided, the session forks from it (reusing cache).
    Otherwise a new session is created. Supports provider-agnostic streaming.
    """
    node = await get_node(node_id)
    if not node:
        return

    tree = await get_tree(node.tree_id)
    if not tree:
        return

    # Resolve the provider type
    provider_type = _PROVIDER_TYPE_MAP.get(provider, ProviderType.CLAUDE)

    system_prompt = _build_system_prompt(node, tree=tree, workspace=workspace)

    # Append tree-level instructions to system prompt if set
    if tree_instructions:
        system_prompt += (
            "\n\n## Tree Instructions\n"
            "The user has set the following instructions for this entire tree. "
            "Follow them for all responses:\n\n"
            + tree_instructions
        )

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
            # No parent node — treat as fresh
            resume_sid = None
            fork = False

    # If we got a session to fork, verify the session file exists
    if resume_sid and fork:
        from store.git import session_file_exists
        if not session_file_exists(workspace, resume_sid):
            log.info("Parent session file missing, starting fresh for node %s", node_id)
            resume_sid = None
            fork = False

    # If context transfer is needed, prepend it
    prompt = user_message
    if prior_context:
        prompt = prior_context + prompt

    # Build the config
    config_kwargs = dict(
        provider=provider_type,
        prompt=prompt,
        cwd=workspace,
        model=model,
        env=_sdk_env(api_key, provider),
    )

    # Only set system_prompt on fresh sessions (not when resuming/forking)
    if resume_sid:
        config_kwargs["resume_session_id"] = resume_sid
        config_kwargs["fork_session"] = fork
    else:
        config_kwargs["system_prompt"] = system_prompt
        config_kwargs["permission_mode"] = "bypassPermissions"

    config = SessionConfig(**config_kwargs)

    async for event in create_session(config):
        yield event


# ── Demo ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    """Standalone demo — streams a prompt through AgentBridge without
    needing the full CodeFission DB/tree infrastructure.

    Usage:
        python -m store.ai "What files are in this directory?"
        python -m store.ai "Explain this codebase" --model claude-sonnet-4-6
    """
    import asyncio
    import sys

    async def main():
        prompt = sys.argv[1] if len(sys.argv) > 1 else "Say hello in one sentence."
        model = "claude-sonnet-4-6"
        if "--model" in sys.argv:
            model = sys.argv[sys.argv.index("--model") + 1]

        cwd = Path.cwd()
        print(f"[cwd: {cwd} | model: {model}]\n")

        # Skip the DB-dependent stream_chat() — use create_session() directly
        # to show the agentbridge layer that stream_chat wraps.
        config = SessionConfig(
            provider=ProviderType.CLAUDE,
            prompt=prompt,
            cwd=cwd,
            model=model,
            system_prompt="You are a helpful coding assistant. Be concise.",
            permission_mode="bypassPermissions",
        )

        session_id = None
        async for event in create_session(config):
            if isinstance(event, SessionInit):
                session_id = event.session_id
            elif isinstance(event, TextDelta):
                print(event.text, end="", flush=True)
            elif isinstance(event, ToolStart):
                print(f"\n[tool: {event.name}]", flush=True)
            elif isinstance(event, ToolEnd):
                status = "ERROR" if event.is_error else "ok"
                print(f"[/{event.name}: {status}]", flush=True)
            elif isinstance(event, TurnComplete):
                parts = []
                if event.cost_usd is not None:
                    parts.append(f"${event.cost_usd:.4f}")
                if event.token_usage:
                    inp = event.token_usage.get("input_tokens", 0)
                    out = event.token_usage.get("output_tokens", 0)
                    parts.append(f"in:{inp} out:{out}")
                meta = " | ".join(parts)
                print(f"\n[done — {meta}]" if meta else "\n[done]")

        # Demo: fork from the session we just created
        if session_id:
            print(f"\n--- Forking from session {session_id[:12]}... ---\n")
            fork_config = SessionConfig(
                provider=ProviderType.CLAUDE,
                prompt="Now briefly summarize what you just said in one bullet point.",
                cwd=cwd,
                model=model,
                system_prompt="You are a helpful coding assistant. Be concise.",
                permission_mode="bypassPermissions",
                resume_session_id=session_id,
                fork_session=True,
            )
            async for event in create_session(fork_config):
                if isinstance(event, TextDelta):
                    print(event.text, end="", flush=True)
                elif isinstance(event, TurnComplete):
                    print("\n[done]")

    asyncio.run(main())
