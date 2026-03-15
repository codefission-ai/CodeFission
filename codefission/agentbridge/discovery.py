"""Detect installed CLI tools, their versions, and auth status."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AuthInfo:
    """Authentication status for a provider."""
    method: str          # "cli_oauth", "api_key", "chatgpt_oauth", "gcloud", "gemini_api_key", "none"
    authenticated: bool
    detail: str = ""     # e.g. email, key prefix, plan name


@dataclass
class ProviderInfo:
    """Everything we can detect about an installed provider."""
    id: str              # "claude-code", "codex"
    name: str            # Human-readable
    installed: bool
    cli_path: str = ""
    version: str = ""
    auth: list[AuthInfo] = field(default_factory=list)
    available_models: list[str] = field(default_factory=list)
    default_model: str = ""

    @property
    def ready(self) -> bool:
        """True if installed and has at least one valid auth method."""
        return self.installed and any(a.authenticated for a in self.auth)


async def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "CLAUDECODE": ""},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
        )
    except (FileNotFoundError, asyncio.TimeoutError, Exception):
        return (-1, "", "")


# ── Claude Code ───────────────────────────────────────────────────────

async def _detect_claude() -> ProviderInfo:
    cli = shutil.which("claude")
    info = ProviderInfo(
        id="claude-code",
        name="Claude Code",
        installed=bool(cli),
        cli_path=cli or "",
        available_models=[
            "claude-sonnet-4-6",
            "claude-opus-4-6",
            "claude-haiku-4-5-20251001",
        ],
        default_model="claude-sonnet-4-6",
    )
    if not cli:
        return info

    # Version: output is like "2.1.74 (Claude Code)"
    rc, out, _ = await _run([cli, "-v"])
    if rc == 0 and out:
        # Extract just the version number
        import re
        m = re.match(r"([\d.]+)", out)
        info.version = m.group(1) if m else out.split("\n")[0].strip()

    # Auth: `claude auth status` returns JSON
    cli_oauth_auth: AuthInfo | None = None
    rc, out, _ = await _run([cli, "auth", "status"], timeout=10)
    if rc == 0 and out:
        try:
            data = json.loads(out)
            if data.get("loggedIn"):
                method = data.get("authMethod", "unknown")
                sub = data.get("subscriptionType", "")
                email = data.get("email", "")
                detail_parts = []
                if email:
                    detail_parts.append(email)
                if sub:
                    detail_parts.append(f"plan: {sub}")
                cli_oauth_auth = AuthInfo(
                    method=f"cli_oauth ({method})",
                    authenticated=True,
                    detail=", ".join(detail_parts),
                )
        except json.JSONDecodeError:
            pass
    info.auth.append(
        cli_oauth_auth
        or AuthInfo(method="cli_oauth", authenticated=False, detail="run: claude login")
    )

    # Also check ANTHROPIC_API_KEY
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        preview = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
        info.auth.append(AuthInfo(
            method="api_key",
            authenticated=True,
            detail=f"ANTHROPIC_API_KEY={preview}",
        ))
    else:
        info.auth.append(AuthInfo(
            method="api_key",
            authenticated=False,
            detail="ANTHROPIC_API_KEY not set",
        ))

    return info


# ── Codex CLI ─────────────────────────────────────────────────────────

async def _detect_codex() -> ProviderInfo:
    cli = shutil.which("codex")
    info = ProviderInfo(
        id="codex",
        name="Codex CLI",
        installed=bool(cli),
        cli_path=cli or "",
        available_models=["o4-mini", "codex-mini", "gpt-5.3-codex"],
        default_model="gpt-5.3-codex",
    )
    if not cli:
        return info

    # Version: output is like "codex-cli 0.101.0"
    rc, out, _ = await _run([cli, "--version"])
    if rc == 0 and out:
        import re
        m = re.search(r"([\d.]+)", out)
        info.version = m.group(1) if m else out.split("\n")[0].strip()

    # Auth: `codex login status`
    cli_api_key = False
    cli_chatgpt = False
    rc, out, err = await _run([cli, "login", "status"], timeout=10)
    status_text = out or err
    if "API key" in status_text:
        preview = ""
        if "sk-" in status_text:
            parts = status_text.split("sk-")
            if len(parts) > 1:
                preview = "sk-" + parts[1].strip()[:12] + "..."
        info.auth.append(AuthInfo(
            method="api_key",
            authenticated=True,
            detail=preview or "OpenAI API key",
        ))
        cli_api_key = True
    if "ChatGPT" in status_text:
        info.auth.append(AuthInfo(
            method="chatgpt_oauth",
            authenticated=True,
            detail="ChatGPT Plus/Pro account",
        ))
        cli_chatgpt = True

    # Also check OPENAI_API_KEY env var
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not cli_api_key:
        if api_key:
            preview = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
            info.auth.append(AuthInfo(
                method="api_key",
                authenticated=True,
                detail=f"OPENAI_API_KEY={preview}",
            ))
        else:
            info.auth.append(AuthInfo(
                method="api_key",
                authenticated=False,
                detail="run: codex login or set OPENAI_API_KEY",
            ))

    if not cli_chatgpt:
        info.auth.append(AuthInfo(
            method="chatgpt_oauth",
            authenticated=False,
            detail="run: codex login --chatgpt",
        ))

    return info


# ── Public API ────────────────────────────────────────────────────────

async def discover() -> list[ProviderInfo]:
    """Detect all supported providers concurrently. Returns list of ProviderInfo."""
    results = await asyncio.gather(
        _detect_claude(),
        _detect_codex(),
    )
    return list(results)


def discover_sync() -> list[ProviderInfo]:
    """Synchronous wrapper for discover()."""
    return asyncio.run(discover())
