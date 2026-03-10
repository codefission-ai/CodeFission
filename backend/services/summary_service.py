"""Auto-name trees by sending context to a small LLM."""

from __future__ import annotations

import asyncio
import logging

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


async def _generate_via_api(prompt: str, model: str, api_key: str) -> str | None:
    """Use the Anthropic SDK directly (requires API key)."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key)
    resp = await client.messages.create(
        model=model,
        max_tokens=30,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


async def _generate_via_cli(prompt: str, model: str) -> str | None:
    """Use `claude -p` subprocess with stdin (works with CLI/OAuth auth)."""
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", "--model", model, "--no-session-persistence",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode()),
            timeout=30,
        )
    except asyncio.TimeoutError:
        proc.kill()
        return None
    if proc.returncode != 0:
        log.debug("claude CLI failed (rc=%d): %s", proc.returncode, stderr.decode(errors="replace")[:200])
        return None
    return stdout.decode(errors="replace").strip()


async def generate_tree_name(
    skill: str,
    repo_info: str,
    first_message: str,
    model: str = "claude-haiku-4-5-20251001",
    auth_mode: str = "cli",
    api_key: str | None = None,
) -> str | None:
    """Generate a short tree name. Uses API key if available, otherwise CLI."""
    try:
        prompt = _format_prompt(skill, repo_info, first_message)

        if api_key and auth_mode == "api_key":
            raw = await _generate_via_api(prompt, model, api_key)
        else:
            raw = await _generate_via_cli(prompt, model)

        if not raw:
            return None
        # Sanitize: first line, strip quotes, cap length
        name = raw.split("\n")[0].strip('"\'').strip()
        return name[:60] if name else None
    except Exception:
        log.warning("Auto-naming failed", exc_info=True)
        return None
