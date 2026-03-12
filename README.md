# CodeFission

Tree-structured AI coding assistant. Each conversation node is an isolated git worktree — branch conversations to explore alternative approaches, and each branch gets its own filesystem sandbox.

## Prerequisites

- **Python 3.12+** — [install](https://www.python.org/downloads/) or via your package manager
- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** — AI backend (spawned as subprocess). Install: `npm install -g @anthropic-ai/claude-code`
- **[git](https://git-scm.com/downloads)** — worktree isolation. Usually pre-installed.

Authenticate Claude Code before first use:

```
claude login
```

## Install

```
pip install codefission
```

Then run:

```
fission
```

Opens on `http://localhost:8080`. Use a different port: `fission 3000`.

## Install from source

Requires [uv](https://docs.astral.sh/uv/installation/) and [Node.js](https://nodejs.org/en/download) 20.19+ or 22.12+.

```
git clone https://github.com/codefission-ai/CodeFission.git
cd CodeFission
make install
fission
```

For development with auto frontend rebuild:

```
make dev
```

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

```
make dev          # install + build frontend + run server
make test         # run tests
make build        # build wheel for PyPI
make publish      # build + upload to PyPI
make clean        # remove build artifacts
```

Frontend dev server (hot reload):

```
cd frontend
npm run dev
```

Data is stored in `~/.codefission/` (SQLite database, git worktrees). Override with `CODEFISSION_DATA_DIR` env var.
