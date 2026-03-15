"""Auto-name trees using the Claude Agent SDK."""

from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger(__name__)

NAMING_PROMPT = """Give this coding project a short name (2-5 words, no quotes, no punctuation). Base it on the context below.

Skill/instructions: {skill}
Repo: {repo}
First message: {message}

Reply with ONLY the project name, nothing else."""


def _format_prompt(skill: str, repo_info: str, first_message: str) -> str:
    return NAMING_PROMPT.format(
        skill=skill or "(none)",
        repo=repo_info or "(empty)",
        message=first_message[:500],
    )


async def generate_tree_name(
    skill: str,
    repo_info: str,
    first_message: str,
    model: str = "claude-haiku-4-5-20251001",
    api_key: str | None = None,
) -> str | None:
    """Generate a short tree name using the Claude Agent SDK.

    Reuses the same auth infrastructure as chat (CLI OAuth or API key).
    No tools, no file access, single turn.
    """
    try:
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, AssistantMessage, query
        from claude_agent_sdk.types import TextBlock
        from store.ai import _sdk_env

        prompt = _format_prompt(skill, repo_info, first_message)

        # Skip the CLI version check — it spawns a subprocess and adds latency
        # while the main chat stream is already running.
        saved = os.environ.get("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK")
        os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] = "1"

        options = ClaudeAgentOptions(
            model=model,
            permission_mode="plan",
            cwd="/tmp",
            env=_sdk_env(api_key or ""),
        )

        text = ""
        gen = query(prompt=prompt, options=options)
        try:
            async with asyncio.timeout(90):
                async for msg in gen:
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                text += block.text
                    elif isinstance(msg, ResultMessage):
                        break
        finally:
            # Ensure the SDK subprocess is cleaned up even on timeout/cancel.
            await gen.aclose()
            # Restore env
            if saved is None:
                os.environ.pop("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", None)
            else:
                os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] = saved

        if not text:
            return None
        name = text.split("\n")[0].strip('"\'').strip()
        return name[:60] if name else None
    except Exception:
        log.warning("Auto-naming failed", exc_info=True)
        return None
