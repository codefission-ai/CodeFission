"""Interactive CLI for testing agentbridge.

Usage:
    python -m agentbridge
    agentbridge              (if pip-installed)

Slash commands:
    /providers       List all detected providers and their status
    /models          List models for the current provider
    /provider <name> Switch to a different provider (e.g. /provider codex)
    /model <name>    Switch to a different model (e.g. /model o4-mini)
    /status          Show current provider, model, and working directory
    /help            Show available commands
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from . import (
    TextDelta,
    ToolStart,
    ToolEnd,
    SessionInit,
    TurnComplete,
    create_session,
)
from .discovery import ProviderInfo
from .session_manager import SessionManager

# ANSI helpers
DIM = "\033[90m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _print_provider(p: ProviderInfo, *, current: bool = False) -> None:
    status = f"{GREEN}ready{RESET}" if p.ready else f"{RED}not ready{RESET}"
    version = f" v{p.version}" if p.version else ""
    installed = f"{GREEN}installed{RESET}" if p.installed else f"{RED}not installed{RESET}"
    marker = f" {YELLOW}<- current{RESET}" if current else ""

    print(f"  {BOLD}{p.id}{RESET} ({p.name}){version}  [{installed}] [{status}]{marker}")

    if not p.installed:
        print(f"    {DIM}CLI not found in PATH{RESET}")
        return

    for auth in p.auth:
        icon = f"{GREEN}✓{RESET}" if auth.authenticated else f"{RED}✗{RESET}"
        print(f"    {icon} {auth.method}", end="")
        if auth.detail:
            print(f"  {DIM}{auth.detail}{RESET}", end="")
        print()

    if p.available_models:
        models = ", ".join(p.available_models)
        print(f"    {DIM}models: {models}{RESET}")


def _print_models(mgr: SessionManager) -> None:
    p = mgr.current_provider
    if not p:
        print(f"{RED}No provider selected{RESET}")
        return
    effective = mgr.effective_model
    print(f"Models for {BOLD}{p.name}{RESET}:")
    for m in p.available_models:
        tags = []
        if m == p.default_model:
            tags.append("default")
        if m == effective:
            tags.append("current")
        suffix = f"  {DIM}({', '.join(tags)}){RESET}" if tags else ""
        print(f"  {m}{suffix}")
    if not p.available_models:
        print(f"  {DIM}(none){RESET}")


def _print_status(mgr: SessionManager, cwd: Path) -> None:
    p = mgr.current_provider
    name = p.name if p else "none"
    print(f"{DIM}Provider: {name} | Model: {mgr.effective_model} | CWD: {cwd}{RESET}")


def _print_help() -> None:
    print(f"""
{BOLD}Commands:{RESET}
  /providers         List all detected providers and their status
  /models            List models for the current provider
  /provider <name>   Switch provider (e.g. {DIM}/provider codex{RESET})
  /model <name>      Switch model (e.g. {DIM}/model o4-mini{RESET})
  /status            Show current provider, model, and working directory
  /help              Show this help
""")


async def run() -> None:
    print(f"{BOLD}AgentBridge{RESET} — unified AI coding CLI interface\n")
    print("Detecting installed providers...")

    mgr = await SessionManager.create()

    print()
    for p in mgr.providers:
        _print_provider(p)
    print()

    ready = mgr.ready_providers
    if not ready:
        print(f"{RED}No providers are ready. Install a CLI and configure auth.{RESET}")
        return

    # ── Initial provider selection ───────────────────────────────────
    print("Select provider (number or name):")
    for i, p in enumerate(ready, 1):
        print(f"  {i}. {p.id} ({p.name})")
    try:
        choice = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    # Resolve choice to provider id
    selected_id: str | None = None
    try:
        idx = int(choice)
        selected_id = ready[idx - 1].id
    except (ValueError, IndexError):
        selected_id = choice

    result = mgr.switch_provider(selected_id)
    if not result.success:
        print(f"{RED}{result.message}{RESET}")
        return

    # ── Initial model selection ──────────────────────────────────────
    p = mgr.current_provider
    if p and p.available_models:
        print(f"\nAvailable models for {p.name}:")
        for j, m in enumerate(p.available_models, 1):
            default_tag = f" {DIM}(default){RESET}" if m == p.default_model else ""
            print(f"  {j}. {m}{default_tag}")
        print(f"\nSelect model (number, name, or Enter for default):")
        try:
            mchoice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if mchoice:
            # Try as number
            model_name: str | None = None
            try:
                midx = int(mchoice)
                model_name = p.available_models[midx - 1]
            except (ValueError, IndexError):
                model_name = mchoice
            mr = mgr.switch_model(model_name)
            if not mr.success:
                print(f"{RED}{mr.message}, using default{RESET}")

    cwd = Path.cwd()
    print()
    _print_status(mgr, cwd)
    print(f"Type your messages. Use /help for commands. Ctrl+C to exit.\n")

    # ── Chat loop ────────────────────────────────────────────────────
    while True:
        prompt_prefix = mgr.current_provider_id or "?"
        try:
            prompt = input(f"[{prompt_prefix}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{DIM}Bye!{RESET}")
            break

        if not prompt:
            continue

        # ── Slash commands ───────────────────────────────────────────
        if prompt.startswith("/"):
            parts = prompt.split(None, 1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd == "/help":
                _print_help()

            elif cmd == "/providers":
                await mgr.refresh()
                print()
                for p in mgr.providers:
                    _print_provider(p, current=(p.id == mgr.current_provider_id))
                print()

            elif cmd == "/models":
                _print_models(mgr)

            elif cmd == "/provider":
                if not arg:
                    cp = mgr.current_provider
                    name = f"{cp.id} ({cp.name})" if cp else "none"
                    print(f"Current provider: {BOLD}{name}{RESET}")
                    print(f"{DIM}Usage: /provider <name>  (e.g. /provider codex){RESET}")
                    continue
                sr = mgr.switch_provider(arg)
                color = GREEN if sr.success else RED
                print(f"{color}{sr.message}{RESET}")
                if sr.success:
                    _print_status(mgr, cwd)

            elif cmd == "/model":
                if not arg:
                    print(f"Current model: {BOLD}{mgr.effective_model}{RESET}")
                    print(f"{DIM}Usage: /model <name>  (e.g. /model o4-mini){RESET}")
                    continue
                sr = mgr.switch_model(arg)
                color = GREEN if sr.success else RED
                print(f"{color}{sr.message}{RESET}")
                if sr.success:
                    _print_status(mgr, cwd)

            elif cmd == "/status":
                _print_status(mgr, cwd)

            else:
                print(f"{RED}Unknown command: {cmd}{RESET}")
                _print_help()

            continue

        # ── Send prompt to provider ──────────────────────────────────
        try:
            config = mgr.build_config(prompt=prompt, cwd=cwd)
        except RuntimeError as e:
            print(f"{RED}{e}{RESET}")
            continue

        try:
            async for event in create_session(config):
                if isinstance(event, SessionInit):
                    print(f"\n{DIM}[session: {event.session_id}]{RESET}")
                elif isinstance(event, TextDelta):
                    print(event.text, end="", flush=True)
                elif isinstance(event, ToolStart):
                    args_preview = ""
                    if event.arguments:
                        args_str = json.dumps(event.arguments)
                        args_preview = args_str[:120]
                        if len(args_str) > 120:
                            args_preview += "..."
                    print(f"\n{CYAN}[tool: {event.name}({args_preview})]{RESET}", flush=True)
                elif isinstance(event, ToolEnd):
                    status = f"{RED}ERROR{RESET}" if event.is_error else f"{GREEN}ok{RESET}"
                    result_preview = (event.result or "")[:200]
                    if len(event.result or "") > 200:
                        result_preview += "..."
                    print(f"{CYAN}[/{event.name or event.tool_call_id}: {status}]{RESET} {result_preview}", flush=True)
                elif isinstance(event, TurnComplete):
                    parts = []
                    if event.duration_ms:
                        parts.append(f"{event.duration_ms}ms")
                    if event.cost_usd is not None:
                        parts.append(f"${event.cost_usd:.4f}")
                    if event.num_turns:
                        parts.append(f"{event.num_turns} turns")
                    if event.token_usage:
                        inp = event.token_usage.get("input_tokens", 0)
                        out = event.token_usage.get("output_tokens", 0)
                        cached = event.token_usage.get("cached_input_tokens", 0)
                        tok_parts = [f"in:{inp}", f"out:{out}"]
                        if cached:
                            tok_parts.append(f"cached:{cached}")
                        parts.append(" ".join(tok_parts))
                    meta = " | ".join(parts)
                    if event.is_error:
                        print(f"\n{RED}[error]{' — ' + meta if meta else ''}{RESET}")
                    else:
                        print(f"\n{DIM}[done{' — ' + meta if meta else ''}]{RESET}")
        except FileNotFoundError as e:
            print(f"\n{RED}Error: {e}{RESET}")
        except Exception as e:
            print(f"\n{RED}Error: {type(e).__name__}: {e}{RESET}")

        print()


def main_sync() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main_sync()
