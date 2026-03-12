# CodeFission

Tree-structured AI coding assistant. Each conversation node is an isolated git worktree — branch conversations to explore alternative approaches, and each branch gets its own filesystem sandbox.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — AI backend (spawned as subprocess). Install: `npm install -g @anthropic-ai/claude-code`
- [uv](https://docs.astral.sh/uv/installation/) — Python package manager. Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- [Node.js](https://nodejs.org/en/download) 18+ — frontend build. Install via system package manager or [nvm](https://github.com/nvm-sh/nvm)
- [git](https://git-scm.com/downloads) — worktree isolation. Usually pre-installed.

Authenticate Claude Code before first use:

```
claude login
```

## Quick start

### Option A: Let Claude do it

If you already have Claude Code installed:

```
claude -p "git clone <repo-url> codefission && cd codefission && ./run.sh"
```

### Option B: Manual

```
git clone <repo-url> codefission
cd codefission
./run.sh
```

On first run, `run.sh` will:
1. Install Python dependencies via `uv` (creates `.venv/` automatically)
2. Install npm packages and build the frontend
3. Start the server on `http://localhost:8080`

Subsequent runs skip steps 1-2 (unless dependencies or source changed).

To use a different port: `./run.sh 3000`

## How it works

Create a tree in the sidebar, type a message, and CodeFission spawns a Claude Code session in an isolated git worktree. Branch any node to explore alternatives — each branch forks the conversation context and the filesystem state.

```
         [root]
        /      \
   [add auth]  [add auth]     <- same prompt, different approaches
      |            |
 [fix tests]  [add logging]   <- independent follow-ups
```

Every node tracks its git branch, commit, and Claude session. Child nodes fork from the parent's prompt cache so context carries over without re-sending history.

## Authentication

Configurable in the Settings panel (gear icon in sidebar):

- **CLI (OAuth)** — default. Uses your `claude login` session. No API key needed.
- **API Key** — provide an Anthropic API key in settings. Useful for headless/remote setups.

Both modes require the Claude Code CLI binary to be installed.

## Configuration

Open Settings (gear icon) to configure:

- **Global defaults** — provider, model, max turns, auth mode. Applies to all trees.
- **Per-tree overrides** — provider, model, max turns. Leave as "Default" to inherit global settings.

Settings persist in the backend database across sessions and devices.

## Development

Run tests:

```
uv run --group dev pytest
```

Frontend dev server (hot reload):

```
cd frontend
npm run dev
```

Data is stored in `~/.codefission/` (SQLite database, git worktrees). Override with `CODEFISSION_DATA_DIR` env var.
