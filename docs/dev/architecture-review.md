# CodeFission — Architecture Review

## What it is

A **tree-structured AI coding assistant** where each conversation node is backed by an isolated git worktree. You branch conversations to explore alternative approaches, and each branch gets its own filesystem sandbox. Think "conversation version control" — fork an AI chat like you fork a git branch.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.12, FastAPI, uvicorn |
| **Database** | SQLite via aiosqlite (WAL mode) |
| **AI** | Claude Agent SDK (primary), with Anthropic + OpenAI provider abstractions |
| **Frontend** | React 19, TypeScript 5.9, Vite 7 |
| **State** | Zustand (client), in-memory dicts (server) |
| **Canvas** | React Flow (@xyflow/react) for the node tree visualization |
| **Comms** | Single WebSocket — all client-server traffic |
| **Markdown** | marked + KaTeX |

## Size

~**3,900 lines** of application code total (1,879 backend, 2,009 frontend). Very compact for what it does.

## Architecture

**Backend** — A monolithic WebSocket handler (`main.py`, 496 lines) that acts as the controller. Instead of REST endpoints, the entire API is a single `/ws` connection with a dispatch table mapping ~13 message types to handler functions. Three service modules provide the business logic:

- `tree_service.py` — CRUD for trees and nodes (SQLite)
- `chat_service.py` — Streams Claude Agent SDK responses as structured events (TextDelta, ToolStart, ToolEnd, SessionInit)
- `workspace_service.py` — Git worktree lifecycle: init, clone, branch, auto-commit, file browsing, diff

**Frontend** — A three-panel layout (sidebar, canvas, chat/files) with a custom Reingold-Tilford-inspired tree layout algorithm (`layout.ts`). The store is plain Zustand with actions as standalone functions rather than inside the store definition.

## Strengths

1. **Clean domain model.** The `Tree -> Node` hierarchy with git worktree isolation is well-mapped. Each node tracks `git_branch`, `git_commit`, and `session_id`, giving a complete snapshot of state per conversation branch.

2. **Session forking.** The Claude Agent SDK session files get copied between worktrees so child nodes fork from the parent's prompt cache. This is a clever optimization that preserves context without re-sending full history.

3. **Structured streaming protocol.** The chat service yields typed dataclass events (`TextDelta`, `ToolStart`, `ToolEnd`) rather than raw strings, keeping the boundary between SDK internals and wire format clean.

4. **The layout algorithm** is genuinely interesting — contour-based with proper handling of expanded node heights spilling across depth levels. Not trivial code, and it includes a 277-line test file.

5. **Path traversal protection** in `read_file()` and the 1MB size cap show security awareness.

6. **Auto-rebuild** in `run.sh` — detects stale frontend builds by comparing mtimes. Simple and effective.

## Concerns

1. **The 496-line WebSocket handler is doing too much.** `main.py` mixes connection management, streaming orchestration, cancellation logic, git operations, and response assembly. The `_run_chat` inner function alone is ~200 lines with deeply nested try/except/finally. This is the single highest-risk file for bugs and is hard to test in isolation.

2. **No HTTP fallback.** The entire API is WebSocket-only. If the connection drops mid-stream, there's no way to recover state — the client reconnects and re-fetches the tree, but any in-flight streaming response is lost. There's no reconnection token or message replay.

3. **Database connection per operation.** Every `get_node()`, `update_node()`, etc. opens and closes a new SQLite connection via the `get_db()` context manager. In a tight streaming loop, `_run_chat` calls `get_node` 5-6 times and `update_node` multiple times. A connection pool or a single connection per WebSocket session would be more efficient.

4. **UUID collision risk.** IDs are `str(uuid.uuid4())[:8]` — 8 hex chars = 32 bits of entropy. With ~65k trees you'd hit a 50% collision probability. Fine for a personal tool, but the truncation is unnecessarily aggressive.

5. **The provider abstraction is unused.** `AnthropicProvider` and `OpenAIProvider` exist with full streaming implementations, but `chat_service.py` exclusively uses `claude_agent_sdk.query()`. These providers look copy-pasted from another project ("WhatTheBot") and are dead code.

6. **No CSS files.** All styles appear to be inline or in `index.html`. For a UI-heavy app with a canvas, resize handles, panels, and tree nodes, this makes visual iteration harder than it needs to be.

7. **`__pycache__` in the repo.** The `.gitignore` lists `__pycache__/` but the `codefission/` directory still has compiled `.pyc` files tracked (visible in the glob). These should be purged from git history.

8. **Limited error recovery.** Cancelled streams leave partial responses with a text marker (`*[Cancelled by user]*`). If the SDK subprocess doesn't clean up properly (the `_silence_asyncgen_gc` handler is a hint this has been an issue), there's no retry mechanism.

## Summary

This is a well-conceived personal/prototype tool with a creative core idea (conversation trees + git worktrees). The code is readable, the data model is sound, and the streaming pipeline is well-structured. The main technical debt is the monolithic WebSocket handler and the dead provider code. For taking this beyond a solo-dev tool, the priorities would be extracting the stream orchestration from `main.py`, adding a connection pool, and building reconnection resilience.
