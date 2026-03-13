# CodeFission Backend Rewrite — CLI-First Architecture

## Guiding Principles

1. **Model-View-Presenter** — two Views (CLI terminal, React GUI) share one Model (Orchestrator + Services). Each has its own Presenter. EventBus handles cross-view notification.
2. **CLI-first** — every *data operation* works from terminal. Visual-only features (canvas layout, note positioning, quote arrows) are GUI-only by nature.
3. **Model owns all logic** — Presenters are thin (dispatch + format). If `handlers.py` is doing validation or computing state, it belongs in the Model.
4. **Audit log** — append-only record of every mutation for `fission log` and debugging. Not event sourcing — trees/nodes tables remain the source of truth for state.
5. **Provider-agnostic** — per-tree provider/model via agentbridge `SessionManager`, changeable at runtime
6. **No sandbox** — delete sandbox code entirely (filesystem rules in system prompt are sufficient)

## Current State

| Component | Lines | Purpose | Verdict |
|-----------|-------|---------|---------|
| `cli.py` | 268 | Git detection + uvicorn launch | **Rewrite** — add full click CLI |
| `main.py` | 424 | FastAPI app, WS + REST endpoints | **Keep** — thin out, remove sandbox hooks |
| `handlers.py` | 1059 | WS dispatch, streaming, 30+ handlers | **Refactor** — pure WS Presenter; move all business logic to Model |
| `orchestrator.py` | 563 | Business logic coordinator | **Rewrite** — add action log, remove sandbox/draft complexity |
| `chat_service.py` | 256 | AgentBridge streaming wrapper | **Update** — resolve provider from tree config, use SessionManager |
| `tree_service.py` | 416 | Tree/Node CRUD + settings | **Simplify** — rename to `trees.py` |
| `workspace_service.py` | ~400 | Git worktrees, auto-commit, files | **Keep** — rename to `workspace.py` |
| `process_service.py` | ~100 | Find/kill orphan processes | **Defer** — keep as-is |
| `sandbox.py` + platform files | ~300 | Filesystem sandboxing | **Delete** |
| `summary_service.py` | ~100 | Summarize conversations | **Defer** — not essential |
| `providers/` | 80 | Static provider registry | **Delete** — replaced by agentbridge |
| `models.py` | ~60 | Node, Tree dataclasses | **Update** — add Action model |
| `db.py` | ~100 | SQLite tables + migrations | **Update** — add `actions` table |
| `config.py` | ~60 | Data dir, project path context | **Keep** |
| `events.py` | ~80 | EventBus + WS constants | **Keep** |

## Architecture: Model-View-Presenter

Two views (terminal CLI, React GUI) share a single Model. Each has its own Presenter that translates between transport protocol and Model calls. The EventBus handles cross-presenter notification so changes from one view appear in the other.

### Layers

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| **Model** | Orchestrator + Services + DB | Business logic, state mutations, action log. Emits domain events via EventBus. Transport-agnostic — no knowledge of WS, HTTP, or terminal. |
| **WS Presenter** | `handlers.py` | Receives WS messages → calls Model → pushes WS responses. Subscribes to EventBus for cross-presenter notifications (e.g. CLI action → UI update). |
| **CLI Presenter** | REST routes in `main.py` | Receives HTTP requests from CLI → calls Model → returns HTTP/SSE responses. EventBus auto-notifies WS Presenter. |
| **GUI View** | React frontend | Renders UI, sends WS messages, receives WS pushes. |
| **CLI View** | `cli.py` (click) | Terminal I/O. Sends HTTP to server when running, or runs Model directly in headless mode. |

### Diagram

```
 ┌───────────┐                              ┌───────────┐
 │  CLI View │                              │  GUI View │
 │ (terminal)│                              │  (React)  │
 └─────┬─────┘                              └─────┬─────┘
       │ HTTP                                     │ WS
       ▼                                          ▼
 ┌───────────────┐                        ┌───────────────┐
 │ CLI Presenter │                        │ WS Presenter  │
 │ (REST routes) │                        │ (handlers.py) │
 └───────┬───────┘                        └───────┬───────┘
         │              ┌──────────┐              │
         └─────────────►│  Model   │◄─────────────┘
                        │(Orch +   │
                        │ Services)│
                        └────┬─────┘
                             │ emit
                        ┌────┴─────┐
                        │ EventBus │──── notifies all presenters
                        └──────────┘
                             │
                  ┌──────┬───┴────┬──────────┐
                  ▼      ▼       ▼           ▼
               trees   chat   workspace   actions
                         │
                         ▼
                    agentbridge
```

### How cross-notification works

The Model returns results to the caller (for direct response) AND emits domain events on EventBus (for broadcast to other presenters). Presenters never call each other — they communicate only through Model + EventBus.

Two distinct response paths:

| Path | Purpose | Example |
|------|---------|---------|
| **Direct return** | Response to the caller who initiated the action | "Here's the tree you just created" |
| **EventBus emit** | Notification to OTHER presenters/clients | "Hey, a tree was created — update your UI" |

```python
# ── Model: orchestrator.py ────────────────────────────────
async def create_tree(self, name, base_branch, base_commit):
    tree = await self.trees.create(name, base_branch, base_commit)
    await self.actions.record("create_tree", tree.id, None,
        {"name": name}, {"tree_id": tree.id})
    await self.bus.emit("tree_created", tree=tree)  # notify OTHER presenters
    return tree  # direct result to caller

# ── WS Presenter: handlers.py ─────────────────────────────
class WSPresenter:
    def __init__(self, bus, ws):
        bus.on("tree_created", self._on_tree_created)

    # Inbound: WS message → call Model → direct response to THIS client
    async def handle_create_tree(self, data):
        tree = await self.orchestrator.create_tree(data["name"], ...)
        await self.send(WS.TREE_CREATED, tree=tree.model_dump())  # direct response

    # EventBus subscriber: broadcast to OTHER clients (e.g. CLI just created a tree)
    async def _on_tree_created(self, tree):
        await self.broadcast(WS.TREE_CREATED, tree_data=serialize(tree))

# ── CLI Presenter: REST route in main.py ──────────────────
@app.post("/api/trees")
async def create_tree(req: CreateTreeRequest):
    tree = await orchestrator.create_tree(req.name, req.branch, req.commit)
    return {"tree_id": tree.id}  # direct response to CLI
    # EventBus already notified WS Presenter → UI updates automatically
```

**Example flow — CLI creates a tree:**

1. CLI View sends `POST /api/trees` to CLI Presenter (REST route)
2. CLI Presenter calls Model (`orchestrator.create_tree()`)
3. Model writes DB + audit log → emits `tree_created` on EventBus → returns tree
4. WS Presenter receives EventBus event → pushes `WS.TREE_CREATED` to all GUI clients
5. CLI Presenter returns HTTP response with tree data → CLI View prints confirmation

The CLI never touches WS. The UI never touches REST. Both see the same state because both go through the same Model.

### Where logic lives

| Concern | Layer |
|---------|-------|
| Validation, state mutations, action recording | **Model** (Orchestrator + Services) |
| WS message parsing/formatting, connection management | **WS Presenter** (handlers.py) |
| HTTP request parsing, response formatting, SSE streaming | **CLI Presenter** (REST routes) |
| Terminal formatting, click commands, user interaction | **CLI View** (cli.py) |
| UI rendering, state management | **GUI View** (React + Zustand) |

**Critical rule**: Presenters contain zero business logic. If `handlers.py` is doing validation, computing diffs, or making decisions — that code belongs in the Model. The current 1059-line `handlers.py` will shrink significantly as logic moves to the Orchestrator.

### CLI vs GUI scope

Not everything the GUI does should have a CLI equivalent. The split:

| Category | CLI | GUI | Notes |
|----------|-----|-----|-------|
| **Trees** — create, list, delete, rename, set settings | Yes | Yes | Same Model operations |
| **Nodes** — branch, select, delete, show details | Yes | Yes | Same Model operations |
| **Chat** — send message, cancel, duplicate | Yes | Yes | CLI streams to terminal, GUI to canvas |
| **Files** — list, diff, read content | Yes | Yes | CLI prints to stdout, GUI has interactive browser |
| **Notes** — create, edit, delete, list | Yes | Yes | CLI is text-only; GUI adds position/size |
| **Settings** — global defaults, tree overrides | Yes | Yes | CLI is `fission set`, GUI is settings panel |
| **Merge** — squash merge to branch | Yes | Yes | |
| **Providers** — list, auth status | Yes | Yes | `fission login` |
| **Action log** — view history | Yes | Yes | `fission log` |
| **Quote files/nodes into chat** | Yes (simplified) | Yes | CLI: `fission chat -q file.py "review"` |
| **Canvas layout** — pan, zoom, node positioning | No | Yes | Inherently visual |
| **Note positioning/sizing** — drag, resize | No | Yes | Spatial metadata, meaningless in terminal |
| **Quote arrows** — visual connections | No | Yes | SVG overlay |
| **Expanded/collapsed state** — node folding in tree | No | Yes | UI layout preference |
| **File attachment** — drag-drop, paste | No | Yes | CLI uses `-q` flag instead |
| **Soft delete with undo toast** | No | Yes | CLI deletes immediately (with `--yes` confirmation) |
| **Theme toggle** — light/dark | No | Yes | Client-local preference |
| **Media preview** — images, video, PDF | No | Yes | CLI shows file path; use `open` to view |

**Notes handling**: Notes store `{id, text, x, y, width, height}` in the tree's `notes` JSON field. The CLI creates/edits note *text* only — `x`, `y`, `width`, `height` are omitted (or zeroed). The GUI auto-positions notes without coordinates at the canvas center. This means CLI-created notes appear correctly in the GUI without any special handling.

**Operations that are GUI-only but still go through the Model**: `set_expanded`, `set_subtree_collapsed` — these persist UI layout state to the DB via the WS Presenter. They're not meaningful CLI operations but they're Model calls (DB writes). The CLI doesn't need them, and they don't need action log entries.

### REST API (CLI Presenter)

```
POST   /api/trees                          # create tree
GET    /api/trees                          # list trees
DELETE /api/trees/:id                      # delete tree
PATCH  /api/trees/:id                      # update tree settings
POST   /api/trees/:id/nodes/:id/branch     # create child node
DELETE /api/trees/:id/nodes/:id            # delete subtree
POST   /api/trees/:id/nodes/:id/chat       # stream chat (SSE response)
POST   /api/trees/:id/nodes/:id/cancel     # cancel active chat
GET    /api/trees/:id/nodes/:id            # get node details
GET    /api/trees/:id/nodes/:id/files      # list files
GET    /api/trees/:id/nodes/:id/diff       # get diff
GET    /api/trees/:id/log                  # get action log
GET    /api/settings                       # get global settings
PATCH  /api/settings                       # update global settings
GET    /api/providers                      # discover providers (agentbridge)
```

### Node = folder + commit

Each node corresponds to a project folder and a commit ID. This is the fundamental unit. A tree is a grouping of related nodes with a shared root — it can be "planted" at any node.

**All project sources converge to the same thing:**

| Source | Setup | Result |
|--------|-------|--------|
| Local git repo | `fission init` (existing) | `repo_path` points to it |
| Uploaded folder | GUI upload → `git init` + `git add .` + `git commit` | Becomes a local git repo |
| New empty folder | `fission init --new <path>` → `mkdir` + `git init` + empty commit | Becomes a local git repo |
| Remote clone | `fission clone <url>` → `git clone` | Becomes a local git repo |

Every path produces a (folder, commit) pair. The data model doesn't care how the repo was created — `Tree.repo_path` + `Tree.base_commit` captures the result.

**Why commit SHA is the stable address, not the node ID:**

The commit SHA is the most stable identifier — repos move, branches get deleted/renamed, but a commit SHA is immutable. However, it can't literally be the node ID because:

1. **Temporal**: the node is created and streaming begins *before* the commit exists. The UI needs an ID immediately at step 2; the commit isn't created until step 5 (after chat completes and `auto_commit()` runs).
2. **Identity**: a node is (code state + conversation), not just code state. Two nodes can share the same commit SHA if neither changed files — they're still different nodes with different conversations.

```
Node identity:  uuid (lifecycle, DB keys, WS routing)
Code address:   git_commit SHA (stable, survives repo moves)
Volatile refs:  git_branch, repo_path (convenience, can change)
```

The system should never rely on `git_branch` or `repo_path` for identity — only for convenience (display, worktree creation). If those change, the commit SHA still points to the right code.

**What a node stores:**

```sql
-- Each row is one node
id              TEXT PRIMARY KEY   -- uuid, assigned at creation (before chat starts)
tree_id         TEXT               -- which tree this node belongs to
parent_id       TEXT               -- points to parent node (NULL = root)
user_message    TEXT               -- what the user said (empty for root)
assistant_response TEXT            -- what the AI said (empty for root)
label           TEXT               -- short display label
status          TEXT               -- idle, active, done, error, draft
created_at      TEXT               -- ISO timestamp
git_branch      TEXT               -- e.g. "ct-abc123" (volatile, for worktree creation)
git_commit      TEXT               -- SHA (NULL while streaming, set on completion)
session_id      TEXT               -- AI provider's session/thread ID (for resume/fork)
provider        TEXT               -- which provider was used: "claude", "codex" (NULL for root)
model           TEXT               -- which model was used: "claude-opus-4-6", "o4-mini" (NULL for root)
created_by      TEXT               -- "human" or "ai"
quoted_node_ids TEXT               -- JSON array of node IDs referenced in this chat
```

`provider` and `model` are recorded per-node because the user can change them mid-tree. This is separate from the tree-level settings (which are defaults for NEW nodes). The node records what was ACTUALLY used.

Example tree where the user switches provider and model mid-conversation:

```
id    parent  user_message        provider  model             session_id   git_commit  status
----  ------  ------------------  --------  ----------------  ----------   ----------  ------
n1    NULL    ""                  NULL      NULL              NULL         abc123      idle
n2    n1      "add auth"          claude    claude-opus-4-6   sess_abc     def456      done
n3    n2      "add tests"         claude    claude-opus-4-6   sess_def     789abc      done
n4    n3      "refactor"          codex     o4-mini           codex_123    bbb222      done
n5    n4      "add logging"       claude    claude-opus-4-6   sess_xyz     ccc333      done
n6    n5      "optimize"          claude    claude-sonnet-4-6 sess_uvw     ddd444      done
```

**Plant tree from any node:**

Any node in any tree has a `git_commit`. You can start a new tree from that state:

```
# Plant from current branch (existing)
fission tree new "Add auth"

# Plant from a specific branch
fission tree new "Experiment" --branch develop

# Plant from an existing node in any tree
fission tree new "Try different approach" --from <node_id>
  → resolves node's git_commit + repo_path
  → creates new tree with base_commit = that commit
```

In the GUI: right-click a node → "Plant new tree here"

The data model supports this already — `create_tree` just needs to accept a `base_commit` directly (instead of always resolving from branch HEAD). Optional provenance: `Tree.forked_from` field to track `{tree_id, node_id}` for display.

### CLI routing

For v1, the CLI always requires the server. This avoids maintaining two code paths (HTTP client vs direct Model) that would inevitably drift. The CLI is a thin HTTP client.

```python
# cli.py — CLI View (simplified)

def _require_server() -> str:
    """Return base URL if server is up, else tell user to start it."""
    lock = Path("~/.codefission/server.lock").expanduser()
    if lock.exists():
        port = lock.read_text().strip()
        try:
            httpx.get(f"http://localhost:{port}/health", timeout=0.5)
            return f"http://localhost:{port}"
        except Exception:
            pass
    click.echo("Server not running. Start it with: fission serve", err=True)
    raise SystemExit(1)

@cli.command()
@click.argument("message")
def chat(message):
    base = _require_server()
    _stream_chat_via_server(base, message)
```

**Why no headless mode in v1**: every CLI command has one implementation (HTTP client). Cross-view sync always works. If headless/CI usage becomes needed later, add it as a separate `--direct` flag.

**`fission serve` is the only command that runs without the server** — it IS the server.

## Files to Change

### Delete

- `codefission/services/sandbox.py` + `_sandbox_linux.py` + `_sandbox_darwin.py`
- `codefission/providers/` directory (replaced by agentbridge discovery + SessionManager)
- References to sandbox in `main.py`, `handlers.py`, `orchestrator.py`

### Rename (no logic changes needed)

- `services/tree_service.py` → `services/trees.py`
- `services/workspace_service.py` → `services/workspace.py`
- `services/chat_service.py` → `services/chat.py`

### Create

- `codefission/services/actions.py` — action log storage + replay
- CLI commands in `codefission/cli.py` (full rewrite with click)

### Update

- `codefission/models.py` — add `Action` dataclass
- `codefission/db.py` — add `actions` table, add `provider`/`model` columns to `nodes`, remove sandbox settings
- `codefission/services/orchestrator.py` — record actions, remove sandbox/draft logic, use SessionManager
- `codefission/services/chat.py` — resolve provider from tree config via SessionManager
- `codefission/handlers.py` — refactor to pure WS Presenter (zero business logic, just dispatch + format + EventBus subscription)
- `codefission/main.py` — remove sandbox hooks
- `codefission/events.py` — add WS constants if needed for new features

### Defer (keep as-is for now)

- `services/process_service.py` + platform backends
- `services/summary_service.py`

## Audit Log

An append-only record of every semantic mutation, regardless of which presenter triggered it. Serves `fission log`, debugging, and cost tracking. **Not event sourcing** — the trees/nodes tables remain the source of truth for current state. The audit log cannot reconstruct state on its own.

### What gets logged vs what doesn't

| Logged (semantic mutations) | NOT logged (UI layout / ephemeral) |
|----|---|
| `create_tree`, `delete_tree`, `rename_tree` | `set_expanded` / `set_subtree_collapsed` (UI fold state) |
| `branch`, `delete_node`, `select` | Note position/size changes (spatial metadata) |
| `chat`, `cancel_chat` | `select_tree` (just remembers last-viewed) |
| `merge` | Theme toggle, sidebar width, canvas zoom |
| `set_global`, `set_tree`, `reset_global`, `reset_tree` | File browsing / diff viewing (read-only) |
| `add_note`, `edit_note`, `delete_note` | |

### Schema

```sql
CREATE TABLE actions (
    id       TEXT PRIMARY KEY,
    seq      INTEGER UNIQUE,    -- auto-incrementing total ordering
    ts       TEXT NOT NULL,      -- ISO 8601 timestamp
    tree_id  TEXT,
    node_id  TEXT,
    kind     TEXT NOT NULL,
    params   TEXT NOT NULL DEFAULT '{}',       -- JSON object
    result   TEXT NOT NULL DEFAULT '{}',       -- JSON object (observations/outcomes)
    source   TEXT NOT NULL DEFAULT 'gui',      -- 'gui' or 'cli'
    FOREIGN KEY (tree_id) REFERENCES trees(id)
);
```

`seq` gives strict ordering. `params` are the inputs; `result` are the outcomes (IDs generated, costs, files changed). `source` distinguishes CLI vs GUI origin for display in `fission log`.

### Action Kinds

| kind | params | result |
|------|--------|--------|
| `create_tree` | `{name, base_branch, base_commit}` | `{tree_id, root_node_id}` |
| `branch` | `{parent_id, label}` | `{node_id, git_branch}` |
| `chat` | `{node_id, message, model, provider}` | `{session_id, cost_usd, token_usage, files_changed, git_commit}` |
| `cancel_chat` | `{node_id}` | `{partial_text_len, active_tools}` |
| `delete_node` | `{node_id}` | `{deleted_count}` |
| `delete_tree` | `{tree_id}` | `{}` |
| `set_global` | `{key, value}` | `{}` |
| `set_tree` | `{key, value}` | `{}` |
| `reset_global` | `{key}` | `{}` |
| `reset_tree` | `{key}` | `{}` |
| `add_note` | `{text}` | `{note_id}` |
| `edit_note` | `{note_id, text}` | `{}` |
| `delete_note` | `{note_id}` | `{}` |
| `merge` | `{node_id, target_branch}` | `{commit, ok}` |
| `select` | `{node_id}` | `{}` |
| `rename_tree` | `{name}` | `{}` |

### Actions Service

```python
# services/actions.py

@dataclass
class Action:
    id: str
    seq: int
    ts: str
    tree_id: str | None
    node_id: str | None
    kind: str
    params: dict
    result: dict
    source: str  # "gui" or "cli"

class ActionLog:
    async def record(self, kind: str, tree_id: str | None, node_id: str | None,
                     params: dict, result: dict | None = None,
                     source: str = "gui") -> Action:
        """Write an action to the log. Returns the action with seq assigned."""
        ...

    async def update_result(self, action_id: str, result: dict) -> None:
        """Update an action's result after async work completes (e.g. chat done)."""
        ...

    async def list_actions(self, tree_id: str, limit: int = 100) -> list[Action]:
        """List actions for a tree, ordered by seq."""
        ...

    async def replay(self, tree_id: str) -> list[Action]:
        """Return all actions for a tree (for reconstruction/export)."""
        ...
```

### Orchestrator Integration

Every orchestrator method records an action. The chat method uses an async generator so presenters stay thin:

```python
async def chat(self, node_id, message, ...):
    # 1. Record action with params
    action = await self.actions.record("chat", tree_id, node_id,
        {"message": message, "model": model, "provider": provider})

    # 2. Prepare and stream — yields domain events for presenter to forward
    ctx = await self.prepare_chat(node_id, message, ...)
    yield ChatNodeCreated(node=ctx.node, after_id=ctx.after_id)

    async for event in self._stream_with_lifecycle(ctx):
        yield event  # TextDelta, ToolStart, ToolEnd

    # 3. Finalize — auto-commit, worktree cleanup, etc.
    result = await self.complete_chat(node_id, ...)
    yield ChatCompleted(result=result)

    # 4. Update action with result
    await self.actions.update_result(action.id, {
        "session_id": result.session_id,
        "cost_usd": result.cost_usd,
        "files_changed": result.files_changed,
        "git_commit": result.git_commit,
    })
```

The WS Presenter and CLI Presenter both consume this generator identically — just forwarding events to their respective transports:

```python
# WS Presenter
async def handle_chat(self, data):
    async for event in self.orchestrator.chat(data["node_id"], data["content"], ...):
        if isinstance(event, ChatNodeCreated):
            await self.send(WS.NODE_CREATED, node=event.node.model_dump())
        elif isinstance(event, TextDelta):
            await self.send(WS.CHUNK, node_id=nid, text=event.text)
        elif isinstance(event, ChatCompleted):
            await self.send(WS.DONE, node_id=nid, ...)

# CLI Presenter (REST route)
@app.post("/api/trees/{tree_id}/nodes/{node_id}/chat")
async def chat(tree_id, node_id, req):
    async def event_stream():
        async for event in orchestrator.chat(node_id, req.message, ...):
            yield f"data: {serialize(event)}\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

## CLI Design

### Entry point

```toml
[project.scripts]
fission = "codefission.cli:main"
```

### State

The CLI tracks an active tree and active node in `~/.codefission/cli_state.json`:

```json
{"tree_id": "abc123", "node_id": "def456"}
```

Commands that need a tree/node use this state. Can be overridden with `--tree` / `--node` flags.

### Commands

```
fission                              # status: project, tree, node, provider
fission init                         # ensure git repo + .codefission/
fission init --new <path>            # mkdir + git init + empty commit
fission clone <url>                  # git clone + fission init
fission serve [--port 19440]         # launch UI server

# ── Trees ────────────────────────────────────────────────
fission tree ls                      # list trees for current repo
fission tree new "Add auth"          # create tree on current branch
fission tree new "Experiment" --branch develop
fission tree new "Alt approach" --from <node_id>   # plant from existing node
fission tree use <id>                # switch active tree
fission tree rename "new name"
fission tree rm <id>                 # delete tree + all nodes + worktrees
fission tree set provider codex      # tree-level override
fission tree set model o4-mini
fission tree set max-turns 10
fission tree set skill "You are a security expert..."

# ── Settings ─────────────────────────────────────────────
fission set                          # show all settings (global + active tree overrides)
fission set provider codex           # set global default provider
fission set model o4-mini            # set global default model
fission set max-turns 10             # set global default max turns (0 = unlimited)
fission set auth-mode api_key        # set auth mode (cli, api_key)
fission set api-key sk-proj-...      # set API key
fission set summary-model claude-haiku-4-5-20251001
fission set --tree provider codex    # explicit tree-level override (same as fission tree set)
fission set --tree model o4-mini
fission set --tree max-turns 5
fission set --tree skill "..."
fission set --reset provider         # clear a global setting (revert to built-in default)
fission set --tree --reset provider  # clear tree override (inherit global)

# ── Nodes ────────────────────────────────────────────────
fission ls                           # show tree structure for active tree
fission select <id>                  # switch active node
fission show [node_id]               # show node details + conversation
fission branch "try alternative"     # create child from active node
fission rm <id>                      # delete node + subtree

# ── Chat ─────────────────────────────────────────────────
fission chat "add refresh tokens"    # chat on active node → creates child
fission chat -q src/file.py "review" # quote a file into context
fission chat --model opus "complex"  # one-off model override

# ── Notes ────────────────────────────────────────────────
fission note ls
fission note add "Remember: tokens expire in 1h"
fission note edit <id> "updated text"
fission note rm <id>

# ── Files & Diff ─────────────────────────────────────────
fission files [node_id]              # list files in node's worktree/commit
fission diff [node_id]               # show unified diff
fission cat <path> [node_id]         # show file contents

# ── Git Integration ──────────────────────────────────────
fission merge <node_id> <branch>     # squash merge node into branch

# ── Action Log ───────────────────────────────────────────
fission log                          # show action history for active tree
fission log --json                   # machine-readable export

# ── Auth / Provider ──────────────────────────────────────
fission login                        # show provider auth status
fission login claude                 # run: claude login
fission login codex                  # run: codex login
```

### Settings Model

Settings follow a two-tier inheritance: **global defaults** (in `settings` table) → **tree overrides** (on `trees` table columns). Empty/null tree values inherit from global. Global defaults fall back to built-in hardcoded values.

```
Built-in defaults  ←  Global settings (DB)  ←  Tree overrides (DB)  ←  CLI one-off flags
```

#### Settable keys

| Key | Global | Tree | Type | Built-in default | Notes |
|-----|--------|------|------|------------------|-------|
| `provider` | `default_provider` | `trees.provider` | string | `"claude-code"` | CodeFission provider ID |
| `model` | `default_model` | `trees.model` | string | provider's default | |
| `max-turns` | `default_max_turns` | `trees.max_turns` | int | `0` (unlimited) | |
| `auth-mode` | `auth_mode` | — | string | `"cli"` | Global only |
| `api-key` | `api_key` | — | string | `""` | Global only, stored in settings table |
| `summary-model` | `summary_model` | — | string | `"claude-haiku-4-5-20251001"` | Global only |
| `skill` | — | `trees.skill` | string | `""` | Tree only (system prompt prefix) |

#### `fission set` output

```
$ fission set
Global defaults:
  provider      claude-code
  model         claude-opus-4-6
  max-turns     0 (unlimited)
  auth-mode     cli
  api-key       (not set)
  summary-model claude-haiku-4-5-20251001

Active tree "Add auth middleware" overrides:
  provider      codex        (global: claude-code)
  model         o4-mini      (global: claude-opus-4-6)
  max-turns     (inherited)
  skill         "You are a security expert..."
```

#### Resolution in orchestrator

`resolve_tree_settings()` already merges tree overrides with global defaults — no change needed. The CLI `fission set` command just writes to the same `settings` table (global) or `trees` columns (tree-level) that the UI settings panel does.

#### Action log entries for settings changes

| kind | params | scope |
|------|--------|-------|
| `set_global` | `{key, value}` | global |
| `set_tree` | `{key, value}` | tree |
| `reset_global` | `{key}` | global |
| `reset_tree` | `{key}` | tree |

### Chat Output Format

```
$ fission chat "add refresh token rotation"
[streaming response with tool calls...]

I've added refresh token rotation to the auth middleware.
Changed 2 files:
  src/auth/tokens.py
  src/auth/middleware.py

[done — $0.0234 | in:1200 out:890]
```

## Provider Resolution + Session Continuity

All provider/model handling goes through agentbridge's `SessionManager`. CodeFission never builds provider-specific commands or parses provider-specific events — agentbridge handles that.

### agentbridge APIs used

| agentbridge API | What CodeFission uses it for |
|---|---|
| `SessionManager.create()` | One-time init — discovers installed providers |
| `SessionManager.apply_settings(provider, model)` | Apply tree-level overrides before each chat |
| `SessionManager.build_config(...)` | Build `SessionConfig` with prompt, cwd, resume/fork, permissions |
| `create_session(config)` | Stream events from AI provider |
| `discover()` | `fission login` and settings panel — show install/auth status |
| `format_history_as_context(history)` | Build text preamble for cross-provider context transfer |

### Session continuity: fork vs context transfer

When a child node chats, it needs the AI to remember the parent's conversation. How this works depends on whether the provider changed:

| Parent's provider | This node's provider | What happens |
|---|---|---|
| Same (any model) | Same (any model) | **Session fork** via agentbridge `resume_session_id` + `fork_session=True` |
| Different | Any | **Context transfer** — walk ancestor chain, build text preamble, pass as `prior_context` |
| None (root) | Any | **Fresh start** — no session to fork, no history to transfer |

**Session fork** (same provider): the AI provider natively continues the conversation. Claude uses `--resume <id> --fork-session`, Codex uses `exec resume <thread_id>`. Changing model within the same provider still works — sessions are provider-level, not model-level.

**Context transfer** (different provider): can't fork across providers. Walk up the ancestor chain, collect `(user_message, assistant_response)` pairs, format them as text, and inject via `SessionConfig.prior_context`. The new AI gets the gist but loses tool call details. Uses agentbridge's `format_history_as_context()`.

### chat.py — the integration point

```python
# services/chat.py (formerly chat_service.py)

from agentbridge import (
    SessionManager, PermissionLevel, create_session, BridgeEvent,
    format_history_as_context, Message, ConversationHistory,
)

# Module-level SessionManager — initialized once, reused across requests
_session_mgr: SessionManager | None = None

async def _get_session_manager() -> SessionManager:
    global _session_mgr
    if _session_mgr is None:
        _session_mgr = await SessionManager.create()
    return _session_mgr

# Map CodeFission provider IDs to agentbridge IDs
_PROVIDER_MAP = {
    "claude-code": "claude",
    "claude": "claude",
    "codex": "codex",
}


def _build_context_from_ancestors(parent_node, all_ancestors: list) -> str:
    """Walk ancestor chain, collect conversations, format as text preamble.

    Uses agentbridge's format_history_as_context() for consistent formatting.
    """
    messages = []
    for node in all_ancestors:
        if node.user_message:
            messages.append(Message(role="user", content=node.user_message))
        if node.assistant_response:
            messages.append(Message(role="assistant", content=node.assistant_response))

    history = ConversationHistory(
        provider=parent_node.provider or "unknown",
        session_id=parent_node.session_id,
        messages=messages,
    )
    return format_history_as_context(history)


async def resolve_session_continuity(parent_node, new_provider: str):
    """Decide: fork parent's session, or build context transfer text.

    Returns (resume_session_id, fork_session, prior_context).
    """
    if not parent_node or not parent_node.user_message:
        # Root node or empty parent — fresh start
        return None, False, None

    if parent_node.session_id and parent_node.provider == new_provider:
        # Same provider — fork the session (works across models)
        return parent_node.session_id, True, None
    else:
        # Different provider or no session — context transfer
        ancestors = await get_ancestor_chain(parent_node.id)  # walk up to root
        prior_context = _build_context_from_ancestors(parent_node, ancestors)
        return None, False, prior_context


async def stream_chat(
    node_id: str,
    user_message: str,
    workspace: Path,
    parent_node,           # Node object — has .provider, .session_id
    *,
    provider: str,         # resolved from tree settings
    model: str,            # resolved from tree settings
    max_turns: int = 0,
    auth_mode: str = "cli",
    api_key: str = "",
) -> AsyncGenerator[BridgeEvent, None]:

    mgr = await _get_session_manager()

    # Apply tree-level provider/model settings via agentbridge
    ab_provider = _PROVIDER_MAP.get(provider, "claude")
    mgr.apply_settings(provider=ab_provider, model=model)

    # Decide: fork parent session or context transfer?
    resume_id, fork, prior_context = await resolve_session_continuity(
        parent_node, ab_provider,
    )

    # Build system prompt
    system_prompt = _build_system_prompt(...)

    # Build config via agentbridge SessionManager
    config = mgr.build_config(
        prompt=user_message,
        cwd=workspace,
        system_prompt=system_prompt,
        max_turns=max_turns if max_turns > 0 else None,
        permission_level=PermissionLevel.AUTONOMOUS,
        resume_session_id=resume_id,
        fork_session=fork,
        prior_context=prior_context,
        env=_sdk_env(auth_mode, api_key),
    )

    # Stream via agentbridge — provider-agnostic
    async for event in create_session(config):
        yield event
```

### What the Orchestrator records on the node

After chat completes, the Orchestrator saves what was actually used:

```python
await update_node(node_id,
    provider=ab_provider,           # "claude" or "codex"
    model=mgr.effective_model,      # "claude-opus-4-6", "o4-mini", etc.
    session_id=session_init.session_id,
    git_commit=commit_sha,
    assistant_response=full_response,
    status="done",
)
```

This means future children can check `parent.provider` to decide fork vs context transfer.

### Discovery

The `fission login` command and the UI settings panel both call agentbridge `discover()` — no static provider registry:

```python
# CLI
@cli.command()
def login():
    from agentbridge import discover_sync
    for p in discover_sync():
        status = "ready" if p.ready else "not ready"
        print(f"  {p.id}  [{status}]  v{p.version}")
        for auth in p.auth:
            icon = "✓" if auth.authenticated else "✗"
            print(f"    {icon} {auth.method}  {auth.detail}")

# WS Presenter (replaces static providers.list_providers())
async def handle_get_providers(self, data):
    from agentbridge import discover
    providers = await discover()
    await self.send(WS.PROVIDERS, providers=[
        {"id": p.id, "name": p.name, "installed": p.installed, "ready": p.ready,
         "version": p.version, "models": p.available_models,
         "default_model": p.default_model,
         "auth": [{"method": a.method, "authenticated": a.authenticated, "detail": a.detail}
                  for a in p.auth]}
        for p in providers
    ])
```

## Implementation Order

### Phase 1: Extract Model from handlers (no breaking changes)

The prerequisite for everything else. Move business logic out of `handlers.py` into the Orchestrator. Existing WS still works — handlers just delegate differently.

1. **Move `_run_chat` streaming logic to Orchestrator** — the hardest piece. Orchestrator exposes an async generator that yields domain events (ChatNodeCreated, TextDelta, ToolStart, ToolEnd, ChatCompleted). The WS Presenter consumes the generator and forwards to WS. Stream lifecycle, PID tracking, timeout/liveness, worktree cleanup all move to Model.
2. **Move remaining handler logic to Orchestrator** — `handle_delete_node` (subtree checks, worktree/branch cleanup, settings cleanup), `handle_update_base` (branch/commit resolution), `handle_open_repo` (find-or-create tree), settings handlers, file operations (list/diff/read with worktree-or-git fallback).
3. **Fix global mutable state** — pass `repo_path` explicitly to service calls instead of `set_project_path()` global. Move `_active_streams` to Orchestrator. This prevents races when WS and REST presenters handle concurrent requests.
4. **Delete sandbox** — remove `sandbox.py` + platform files + all references. Clean cut.

After Phase 1: `handlers.py` is a thin WS Presenter (~300 lines). All business logic is in the Orchestrator. Existing WS protocol unchanged.

### Phase 2: Audit log + REST API

5. **Add audit log** — `services/actions.py` + `Action` model + DB migration. Wire into Orchestrator methods.
6. **Add REST routes** (CLI Presenter) — thin FastAPI routes in `main.py` that call the same Orchestrator methods. EventBus notifies WS Presenter automatically.

### Phase 3: CLI View

7. **Rewrite `cli.py`** — click-based, all commands listed above. Pure HTTP client to the REST routes.
8. **Add CLI state** — `cli_state.json` tracking active tree/node.

### Phase 4: Provider-agnostic chat

9. **Update `chat.py`** — resolve provider from tree config via `_PROVIDER_MAP` + `SessionManager`
10. **Update Orchestrator** — pass provider + model through ChatContext
11. **Delete `providers/`** — replace `list_providers()` calls with `agentbridge.discover()`

### Phase 5: Cleanup

12. **Rename services** — `tree_service.py` → `trees.py`, etc.
13. **Update tests** — fix imports, add CLI tests, add audit log tests

### Deferred

- Process service cleanup (keep working, revisit later)
- Summary service (not essential, defer)
- Headless/direct mode for CLI (add `--direct` flag if CI needs it)

## What NOT to Change

- **Frontend** — no React/TypeScript changes needed. WS protocol stays the same. Provider list endpoint returns same shape (just sourced from agentbridge discovery instead of static registry). GUI-only features (canvas, note positioning, quote arrows) remain frontend-only — they already work and don't affect the CLI.
- **Git worktree logic** — `workspace.py` is solid, no changes needed.
- **EventBus API** — promoted to core architecture (cross-presenter notification), but the existing API (`on`, `off`, `emit`) is fine as-is. Add new event names as needed.
- **REST file endpoints** — upload/download/zip unchanged.
- **DB schema for trees/nodes** — mostly unchanged. One optional addition: `trees.forked_from` (JSON `{tree_id, node_id}` or NULL) to track provenance when planting from a node.
- **Notes data model** — notes store `{id, text, x, y, width, height}` in tree's `notes` JSON field. CLI creates notes with text only (no spatial data). GUI auto-positions notes without coordinates. No schema change needed.

## Verification

1. Run the full CLI workflow:
   ```
   fission init
   fission tree new "test"
   fission chat "hello"
   fission branch "alt"
   fission chat "try something else"
   fission log
   ```
2. Run `fission serve` and verify UI still works
3. Run existing tests (update imports for renames)
4. Verify `fission log --json` output can reconstruct the tree
5. Test provider switching:
   ```
   fission tree set provider codex
   fission tree set model o4-mini
   fission chat "hello from codex"
   ```
6. Test settings:
   ```
   fission set                          # show all
   fission set provider codex           # global default
   fission set --tree model o4-mini     # tree override
   fission set --tree --reset model     # clear tree override → inherits global
   fission set                          # verify inheritance
   ```

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Breaking WS protocol | Keep all WS message types identical; only change source of data |
| `_run_chat` extraction is complex | 250 lines of mixed transport/logic. Split strategy: async generator for domain events, presenter loop for WS forwarding. Test with existing WS first before adding REST. |
| Global `set_project_path()` races | Phase 1 step 3: pass `repo_path` explicitly. This is a prerequisite for concurrent presenters. |
| `_active_streams` ownership | Move to Orchestrator. Presenters register send callbacks; Orchestrator manages lifecycle. |
| Audit log adds latency | Single SQLite write per action — negligible |
| SessionManager stale state | `refresh()` on provider list requests; stateless per-request in WS handler |
| Renames break imports | Batch renames in one commit; grep + update all importers |
| CLI state conflicts with UI | CLI state is advisory — UI uses its own selection state via WS. No conflict. |
| EventBus `create_task` swallows errors | Acceptable for v1. If cross-presenter notifications start failing silently, add error logging to EventBus.emit. |
