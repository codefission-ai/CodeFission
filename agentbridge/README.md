# AgentBridge

Unified async Python interface for AI coding CLI tools. Spawns [Claude Code](https://github.com/anthropics/claude-code) and [Codex CLI](https://github.com/openai/codex) as subprocesses, parses their JSONL event streams, and emits a common set of event types — so your application code doesn't need to know which provider is running.

## Features

- **Unified event stream** — `TextDelta`, `ToolStart`, `ToolEnd`, `SessionInit`, `TurnComplete` regardless of provider
- **Unified permission levels** — `AUTONOMOUS`, `AUTO_EDIT`, `INTERACTIVE` map to each provider's native mode; `CUSTOM` passes through provider-specific values
- **Provider discovery** — detect installed CLIs, versions, and auth status
- **Pricing estimation** — Claude reports cost directly; Codex cost is computed from token counts and a pricing table
- **Cross-provider context transfer** — switch providers mid-session by injecting conversation history as a text preamble
- **Session resume/fork** — Claude native resume + fork; Codex thread resume
- **Runtime provider/model switching** — `SessionManager` validates and tracks state; usable from CLI or app
- **Zero runtime dependencies** — only the Python standard library

## Installation

```bash
pip install -e .
```

Requires Python 3.10+ and at least one CLI installed:


| Provider    | Install                                    | Auth                                           |
| ----------- | ------------------------------------------ | ---------------------------------------------- |
| Claude Code | `npm install -g @anthropic-ai/claude-code` | `claude auth login` or set `ANTHROPIC_API_KEY` |
| Codex CLI   | `npm install -g @openai/codex`             | `codex login` or set `OPENAI_API_KEY`          |


## Quick start

### Interactive CLI

```bash
agentbridge
# or
python -m agentbridge
```

Detects installed providers, lets you pick one, and starts a chat loop with colored output showing tool calls, results, token usage, and cost. Supports slash commands for switching at runtime:


| Command            | Description                                         |
| ------------------ | --------------------------------------------------- |
| `/providers`       | List all providers and their install/auth status    |
| `/models`          | List models for the current provider                |
| `/provider <name>` | Switch provider (e.g. `/provider codex`)            |
| `/model <name>`    | Switch model (e.g. `/model o4-mini`)                |
| `/status`          | Show current provider, model, and working directory |
| `/help`            | Show all commands                                   |


### Programmatic usage

```python
import asyncio
from agentbridge import (
    create_session,
    SessionConfig,
    ProviderType,
    TextDelta,
    ToolStart,
    ToolEnd,
    TurnComplete,
)

async def main():
    async for event in create_session(SessionConfig(
        provider=ProviderType.CLAUDE,
        prompt="List the Python files in this directory",
    )):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, ToolStart):
            print(f"\n[tool: {event.name}]")
        elif isinstance(event, ToolEnd):
            print(f"[result: {event.result[:100]}]")
        elif isinstance(event, TurnComplete):
            print(f"\n[done — cost: ${event.cost_usd:.4f}]")

asyncio.run(main())
```

## API reference

### SessionManager

The core state manager for provider/model selection. Used by both the interactive CLI and application code (e.g. CodeFission). Validates all switches and returns `SwitchResult` objects so callers can report status without coupling to presentation.

```python
import asyncio
from agentbridge import SessionManager, create_session, TextDelta

async def main():
    # Create with auto-discovery
    mgr = await SessionManager.create()

    # Select provider
    result = mgr.switch_provider("claude")
    print(result.message)  # "Switched to Claude Code"

    # Switch model
    result = mgr.switch_model("claude-opus-4-6")
    print(result.message)  # "Switched to model claude-opus-4-6"

    # Query state
    print(mgr.current_provider_id)  # "claude"
    print(mgr.effective_model)      # "claude-opus-4-6"
    print(mgr.available_models)     # ["claude-sonnet-4-6", "claude-opus-4-6", ...]

    # Switch provider mid-conversation (resets model to new provider's default)
    result = mgr.switch_provider("codex")
    print(result.model)  # "gpt-5.3-codex"

    # Build config and run
    config = mgr.build_config(prompt="List files in this directory")
    async for event in create_session(config):
        if isinstance(event, TextDelta):
            print(event.text, end="")

asyncio.run(main())
```

#### Integrating with app-level settings

For apps like CodeFission where provider/model is stored per-tree or globally, use `apply_settings()` to merge overrides. Empty strings mean "keep current":

```python
# App resolves tree settings: provider="codex", model="" (inherit default)
result = mgr.apply_settings(provider="codex", model="")
# result.provider_id == "codex", result.model == "gpt-5.3-codex"

# Tree overrides both
result = mgr.apply_settings(provider="claude", model="claude-haiku-4-5-20251001")
# result.provider_id == "claude", result.model == "claude-haiku-4-5-20251001"

# Build config with per-request overrides
config = mgr.build_config(
    prompt=user_message,
    cwd=workspace_path,
    max_turns=tree_settings["max_turns"],
    resume_session_id=parent_session_id,
    fork_session=True,
)
```

#### SwitchResult

All mutations return a `SwitchResult`:

```python
@dataclass
class SwitchResult:
    success: bool      # whether the switch was applied
    message: str       # human-readable status ("Switched to ...", "Already using ...", error)
    provider_id: str   # current provider after the operation
    model: str         # current effective model after the operation
```

### Provider discovery

Detect what's installed and ready before starting a session:

```python
from agentbridge import discover_sync, ProviderType

for provider in discover_sync():
    print(f"{provider.name} v{provider.version}")
    print(f"  installed: {provider.installed}")
    print(f"  ready: {provider.ready}")  # installed + authenticated
    print(f"  models: {provider.available_models}")
    for auth in provider.auth:
        print(f"  auth: {auth.method} ({'ok' if auth.authenticated else 'missing'})")
```

### Session configuration

```python
from pathlib import Path
from agentbridge import SessionConfig, ProviderType, PermissionLevel

config = SessionConfig(
    provider=ProviderType.CODEX,
    prompt="Refactor the main module",
    cwd=Path("/path/to/project"),                    # working directory for the agent
    model="o4-mini",                                  # optional model override
    system_prompt="Be concise",                       # Claude only
    max_turns=5,                                      # Claude only
    permission_level=PermissionLevel.AUTONOMOUS,      # unified — works for any provider
    extra_args=["--flag"],                            # escape hatch for arbitrary CLI flags
)
```

### Permission levels

Permissions follow a two-tier model: use a **unified level** for portable behavior, or drop to **custom** for provider-specific control.

#### Unified levels

Set `permission_level` on `SessionConfig` — it maps to the native flag automatically:


| `PermissionLevel` | Claude (`--permission-mode`) | Codex (`--sandbox`) | Description                                  |
| ----------------- | ---------------------------- | ------------------- | -------------------------------------------- |
| `AUTONOMOUS`      | `bypassPermissions`          | `full-auto`         | Full trust — no prompts                      |
| `AUTO_EDIT`       | `acceptEdits`                | `auto-edit`         | Auto-approve file edits, prompt for commands |
| `INTERACTIVE`     | `default`                    | `suggest`           | Prompt for everything                        |


```python
from agentbridge import SessionConfig, ProviderType, PermissionLevel

# Same config works for both providers — just swap ProviderType
config = SessionConfig(
    provider=ProviderType.CLAUDE,
    prompt="Fix the tests",
    permission_level=PermissionLevel.AUTONOMOUS,
)
```

#### Custom (provider-specific)

For modes that only exist on one provider (Claude's `plan`, `dontAsk`, etc.), use `CUSTOM` and set the provider-specific field:

```python
# Claude-only "plan" mode (read-only, no file modifications)
config = SessionConfig(
    provider=ProviderType.CLAUDE,
    prompt="Analyze the codebase",
    permission_level=PermissionLevel.CUSTOM,
    permission_mode="plan",
)

# Codex-specific sandbox mode
config = SessionConfig(
    provider=ProviderType.CODEX,
    prompt="Edit files",
    permission_level=PermissionLevel.CUSTOM,
    sandbox_mode="workspace-write",
)
```

**Validation**: setting a unified level (`AUTONOMOUS`, `AUTO_EDIT`, `INTERACTIVE`) alongside `permission_mode` or `sandbox_mode` raises `ValueError` — pick one approach or the other. You can also omit `permission_level` entirely and use the provider-specific fields directly for backward compatibility.

### Event types

All events inherit from `BridgeEvent` and carry `kind`, `provider`, and `raw` (original JSON) fields.


| Event          | Fields                                                                          | Description                                                                       |
| -------------- | ------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `SessionInit`  | `session_id`                                                                    | Emitted once when the session/thread ID is known                                  |
| `TextDelta`    | `text`                                                                          | Incremental text from the assistant (streamed for Claude, single chunk for Codex) |
| `ToolStart`    | `tool_call_id`, `name`, `arguments`                                             | A tool invocation has started                                                     |
| `ToolEnd`      | `tool_call_id`, `name`, `result`, `is_error`                                    | A tool invocation has completed                                                   |
| `TurnComplete` | `session_id`, `is_error`, `duration_ms`, `cost_usd`, `num_turns`, `token_usage` | The agent finished its turn                                                       |


### Session resume and fork

```python
# Resume an existing Claude session
config = SessionConfig(
    provider=ProviderType.CLAUDE,
    prompt="Continue where you left off",
    resume_session_id="session-abc-123",
)

# Fork from an existing Claude session (creates a branch)
config = SessionConfig(
    provider=ProviderType.CLAUDE,
    prompt="Try a different approach",
    resume_session_id="session-abc-123",
    fork_session=True,
)

# Resume a Codex thread
config = SessionConfig(
    provider=ProviderType.CODEX,
    prompt="What did we do last time?",
    resume_session_id="thread-xyz-789",
)
```

### Pricing estimation

Claude reports `total_cost_usd` directly in its result event. For Codex, cost is computed from token counts:

```python
from agentbridge import estimate_cost, TokenUsage, PRICING_TABLE

# Manual estimation
usage = TokenUsage(input_tokens=100_000, output_tokens=50_000, cached_input_tokens=10_000)
cost = estimate_cost("o4-mini", usage)
print(f"${cost:.4f}")

# Or from raw event data (used internally by the Codex adapter)
from agentbridge import estimate_cost_from_raw
cost = estimate_cost_from_raw("gpt-5.3-codex", {"usage": {"input_tokens": 100_000, "output_tokens": 50_000}})

# Add or update model pricing
from agentbridge import ModelPricing
PRICING_TABLE["new-model"] = ModelPricing(
    input_per_mtok=2.0,
    output_per_mtok=8.0,
    cached_input_per_mtok=0.5,
)
```

### Cross-provider context transfer

Switch from one provider to another while preserving conversation context:

```python
import asyncio
from dataclasses import asdict
from agentbridge import (
    create_session,
    create_session_with_context,
    SessionConfig,
    ProviderType,
    TextDelta,
    TurnComplete,
)

async def main():
    # Phase 1: Start with Claude
    claude_events = []
    async for event in create_session(SessionConfig(
        provider=ProviderType.CLAUDE,
        prompt="Analyze the codebase and find potential bugs",
    )):
        claude_events.append(asdict(event))
        if isinstance(event, TextDelta):
            print(event.text, end="")

    # Phase 2: Continue with Codex, injecting Claude's context
    async for event in create_session_with_context(
        SessionConfig(
            provider=ProviderType.CODEX,
            prompt="Fix the bugs that were found",
        ),
        prior_events=claude_events,
    ):
        if isinstance(event, TextDelta):
            print(event.text, end="")

asyncio.run(main())
```

Or build the context manually for more control:

```python
from agentbridge import extract_history, format_history_as_context, SessionConfig, ProviderType

history = extract_history(claude_events)
context = format_history_as_context(history)
# context is a readable text block like:
# [Context from previous claude session abc-123]
# Assistant: I found 3 potential bugs...
# [Tool: Bash]
# [Result: grep output...]
# [End of previous context]

config = SessionConfig(
    provider=ProviderType.CODEX,
    prompt="Fix the bugs",
    prior_context=context,  # injected as preamble to the prompt
)
```

## Architecture

```
agentbridge/
  __init__.py           # Public API: create_session, create_session_with_context
  __main__.py           # Interactive CLI harness (uses SessionManager)
  session_manager.py    # Stateful provider/model selection and config building
  types.py              # ProviderType, PermissionLevel enums, SessionConfig dataclass
  events.py             # Unified event types (BridgeEvent, TextDelta, etc.)
  base.py               # BaseAdapter ABC
  subprocess_runner.py  # Async subprocess with JSONL parsing
  discovery.py          # Detect installed CLIs, versions, auth
  pricing.py            # Model pricing table and cost estimation
  context.py            # Cross-provider conversation history transfer
  adapters/
    __init__.py         # Adapter registry
    claude.py           # Claude Code CLI adapter
    codex.py            # Codex CLI adapter
```

Each adapter translates provider-specific JSONL events into unified `BridgeEvent` types:

- **Claude** streams token-level deltas via `content_block_delta` events and reports cost directly
- **Codex** emits complete text on `item.completed` and reports token counts (cost computed from pricing table)

## Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

136 tests covering events, types, permission levels, pricing, context transfer, session manager (switch/apply/build_config), adapter command building, adapter streaming with mocked subprocess output, and end-to-end cross-provider round-trips.