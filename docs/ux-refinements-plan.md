# Architecture Redesign: Single Server, One Tree Per Project

## Context

CodeFission's current model runs one server per project with multiple trees per project. This redesign simplifies to:
- **Single global server** — one fission process manages all projects
- **One tree per project** — no tree picker, the tree IS the project's exploration history
- **Canvas-first UX** — sidebar starts closed, user sees the tree immediately
- **Root pinned to creation commit** — snapshot in time, not a moving target

---

## Core Model

```
fission (single server, one process)
  └── project A (/path/to/project-a)
  │     └── one tree (root pinned to commit abc123 on main)
  │           ├── root node (read-only, shows snapshot)
  │           ├── child node 1 (ct-{id} branch, own worktree)
  │           └── child node 2 (ct-{id} branch, own worktree)
  └── project B (/path/to/project-b)
        └── one tree (root pinned to commit def456 on develop)
              └── ...
```

- Each project has exactly one tree, auto-created on first open
- Root node is pinned to `base_commit` (the branch HEAD at tree creation time)
- Child nodes branch from parent commits into isolated worktrees
- A protective git ref (`refs/codefission/{tree_id}`) prevents GC of the base commit
- Per-project data lives in `{project}/.codefission/` (DB, worktrees, artifacts)

---

## Feature 1: Single Global Server

### 1a. CLI becomes a thin client

**File:** `codefission/cli.py`

New behavior for `fission [path]`:
1. Check if a fission server is already running (read `~/.codefission/server.lock`)
   - If running: register the project, open browser to `http://localhost:{port}?project=/path`, exit
   - If not running: start the server, acquire lock, register project, open browser
2. `fission` with no path and not in a git dir → start server in hub mode (project list)
3. `fission` in a git dir → start server (or reuse running one), open that project

**Port selection:**
- Default port: `19440` (uncommon, easy to remember — "fission" vibes)
- If default is taken, auto-scan for next available port (try up to 10 ports: 19440–19449)
- `--port PORT` flag overrides with an explicit port (no auto-scan, fail if taken)
- The chosen port is stored in the lock file so subsequent `fission` calls know where to connect

**Global lock file:** `~/.codefission/server.lock` (not per-project)
```json
{"pid": 12345, "port": 19440, "started_at": "2026-03-12T10:00:00Z"}
```

Functions:
- `_find_available_port(preferred)`: Try `preferred`, then `preferred+1` ... `preferred+9`. Return first available. Raise if none found.
- `_acquire_global_lock(port)`: Check PID liveness. If alive, return the running port. If stale, overwrite.
- `_release_global_lock()`: Delete on exit via `atexit.register()`.

### 1b. Project Registry

**New file:** `codefission/registry.py`

Registry at `~/.codefission/projects.json`:
```json
[
  {"path": "/abs/path", "name": "project-name", "last_opened": "2026-03-12T10:00:00Z"}
]
```

Functions:
- `register_project(path)` — add/update entry with current timestamp
- `list_projects()` — return all entries sorted by last_opened desc
- `remove_project(path)` — remove entry

Called from `cli.py` whenever `fission` is run in a project dir.

### 1c. Multi-project backend support

**File:** `codefission/config.py`

- Remove module-level `PROJECT_PATH` / `PROJECT_DIR` globals
- Add `DATA_DIR = Path.home() / ".codefission"` (global data: lock, registry, settings)
- Project path becomes per-request context, not global state

**File:** `codefission/db.py`

- Support multiple DB connections: one per project, opened on demand
- `get_db(project_path)` → returns/caches the aiosqlite connection for that project's `.codefission/codefission.db`
- `close_all_dbs()` on shutdown

**File:** `codefission/services/workspace_service.py`

- All functions that use `PROJECT_PATH` gain a `project_path: Path` parameter
- `resolve_workspace(project_path, root_id, node_id)`
- `create_worktree(project_path, node_id, from_commit)`
- `ensure_worktree(project_path, root_id, node_id, parent_id, parent_commit)`
- etc.

**File:** `codefission/handlers.py`

- `ConnectionHandler` tracks the active project path for each WebSocket connection
- `handle_open_project(data)`: sets the active project, inits DB if needed, loads the single tree
- `handle_list_projects(data)`: returns registry entries
- All existing handlers pass `self.project_path` through to services

**File:** `codefission/services/orchestrator.py`

- `Orchestrator.__init__(project_path)` — scoped to a project
- All methods use `self.project_path` instead of global `PROJECT_PATH`

### 1d. WS Protocol Changes

**File:** `codefission/events.py`

New message types:
- `LIST_PROJECTS` / `PROJECTS` — list all registered projects
- `OPEN_PROJECT` / `PROJECT_OPENED` — switch active project context
- `REMOVE_PROJECT` — unregister a project

Remove (no longer needed):
- `CREATE_TREE`, `DELETE_TREE`, `LIST_TREES` — one tree per project, auto-managed

Simplify:
- `LOAD_TREE` → implicit on `OPEN_PROJECT` (load the single tree)

---

## Feature 2: One Tree Per Project

### 2a. Auto-creation

**File:** `codefission/services/orchestrator.py`

When a project is opened and has no tree in its DB:
- Detect current branch: `git rev-parse --abbrev-ref HEAD`
- Get HEAD commit: `git rev-parse HEAD`
- Create tree with `base_branch` and `base_commit`
- Create root node pinned to that commit
- Create protective ref: `git update-ref refs/codefission/{tree_id} {base_commit}`

When a project already has a tree:
- Load and return it directly

### 2b. No tree CRUD UI

- No tree creation form
- No tree deletion button (user can `rm -rf .codefission/` to reset)
- No tree list / tree picker
- The tree name = project name (derived from directory basename)

### 2c. Protective Git Ref

**File:** `codefission/services/workspace_service.py`

- On tree creation: `git update-ref refs/codefission/{tree_id} {base_commit}` in the project repo
- Prevents `git gc` from collecting the base commit even if the branch is deleted
- On project removal (if we add that): `git update-ref -d refs/codefission/{tree_id}`

---

## Feature 3: Canvas-First UX & Sidebar

### 3a. Sidebar Open/Closed Logic

**File:** `frontend/src/App.tsx`

- **Sidebar starts closed** if fission was launched from a git project (URL has `?project=...`)
  - User lands directly on the canvas with their tree
- **Sidebar starts open** if fission was launched without a project (hub/home mode, or no `?project=` param)
  - User sees the project list to pick a project

**File:** `frontend/src/store.ts`

- Add: `sidebarOpen: boolean` (default determined by launch context)
- Action: `toggleSidebar()`

### 3b. Sidebar Content — Project List

**File:** `frontend/src/components/TreeList.tsx` → rename to `ProjectList.tsx`

The sidebar is a project list. Each entry displays a **project name** that is determined automatically but editable:

**Default naming rules (auto-name):**
- Git project with GitHub remote → GitHub path (e.g., `user/repo`)
- Git project, no remote → local directory basename (e.g., `my-project`)
- Non-git project (newly initialized) → directory basename, renamed automatically if the user later adds a remote

**Editable name:**
- Double-click the project name in sidebar → inline text input to rename
- Sends `RENAME_PROJECT` to backend, which updates `projects.json` registry
- Custom name overrides auto-detection permanently (stored in registry)

**Each project entry shows:**
- Project name (editable)
- Last opened time (relative: "2h ago")
- Base branch badge
- Staleness indicator (amber dot if base branch has advanced)
- Active project highlighted
- Clicking a project sends `OPEN_PROJECT`, loads its tree on canvas

### 3c. Project Naming — Backend

**File:** `codefission/registry.py`

Registry entry gains a `custom_name` field:
```json
{
  "path": "/abs/path",
  "name": "auto-detected-name",
  "custom_name": null,
  "last_opened": "2026-03-12T10:00:00Z"
}
```

- `name` is always auto-detected (from git remote or basename)
- `custom_name` overrides `name` when set (user edited)
- Display name = `custom_name ?? name`

Auto-detection logic in `register_project()`:
1. Try `git remote get-url origin` → parse GitHub/GitLab path (e.g., `user/repo`)
2. Fallback: `project_path.name` (directory basename)

**File:** `codefission/events.py`

- Add: `RENAME_PROJECT` (inbound) / `PROJECT_RENAMED` (outbound)

**File:** `codefission/handlers.py`

- `handle_rename_project(data)`: updates `custom_name` in registry, broadcasts `PROJECT_RENAMED`

### 3d. On Connect

**File:** `frontend/src/ws.ts`

On WebSocket open:
- Send `LIST_PROJECTS` (populate sidebar)
- If URL has `?project=/path`:
  - Send `OPEN_PROJECT` with that path
  - Set `sidebarOpen = false` (canvas-first)
- Otherwise:
  - Set `sidebarOpen = true` (show project list)
  - Show welcome/empty state on canvas

On `PROJECT_OPENED` response:
- Receive project info + tree + all nodes
- Render the tree on canvas immediately

---

## Feature 4: Interactive Git Init Prompt

**File:** `codefission/cli.py`

When `fission` is run in a non-git directory:
1. Check `sys.stdin.isatty()`. If not TTY → print error, exit.
2. `input("This directory is not a git repo. Initialize one? [Y/n] ")`
3. `y/Y` or empty → `_auto_init_repo()`. Anything else → exit.

Existing `_auto_init_repo()` unchanged — just gated by the prompt.

---

## Feature 5: Branch/Commit Staleness Detection

### 5a. Backend

**File:** `codefission/services/workspace_service.py`

```python
async def check_staleness(project_path: Path, base_branch: str, base_commit: str | None) -> dict:
    # Returns {"stale": False} or {"stale": True, "commits_behind": N, "branch_head": "sha"}
```

### 5b. Expose on Project Open

**File:** `codefission/handlers.py`

- Include staleness info in `PROJECT_OPENED` response
- Frontend shows indicator on root node and in sidebar project entry

### 5c. Merge Safety

**File:** `codefission/services/orchestrator.py`

- Before merge: check staleness, return soft warning if stale
- Client can re-send with `force: true` to proceed

### 5d. Update Base (Rebase)

**File:** `codefission/services/orchestrator.py`

`update_tree_base(project_path, tree_id)`:
- Get current HEAD of `base_branch`
- Update `base_commit` and root node's `git_commit`
- Update protective ref
- NOT a git rebase — just moves the recorded baseline

**File:** `codefission/events.py`

- `UPDATE_BASE` (inbound) / `BASE_UPDATED` (outbound)

### 5e. Frontend

**File:** `frontend/src/components/TreeNode.tsx`

- Root node: if stale, banner "{branch} has {N} new commits" + "Update base" button
- Staleness info stored in project info, not separate state

---

## Feature 6: Merge Result UX

**File:** `frontend/src/store.ts`

- Add: `mergeResult: {nodeId, ok, commit?, error?, conflicts?} | null`

**File:** `frontend/src/ws.ts`

- Implement `MERGE_RESULT` handler (currently TODO):
  - Store result, refresh project info on success

**File:** `frontend/src/components/TreeNode.tsx`

- Inline feedback: green badge on success, amber conflict list on failure
- Loading state on merge button while in progress

---

## Implementation Order

1. **Feature 4** (git init prompt) — 5 lines, self-contained in cli.py
2. **Feature 2** (one tree per project) — simplify backend, remove tree CRUD
3. **Feature 1** (single server) — biggest change: multi-project support, registry, CLI thin client
4. **Feature 3** (canvas-first UX) — sidebar collapse, project list, layout changes
5. **Feature 5** (staleness detection) — backend check + frontend indicators
6. **Feature 6** (merge result UX) — fill existing TODO

---

## Key File Changes Summary

| File | Changes |
|------|---------|
| `codefission/cli.py` | Thin client: detect running server, open browser, or start server. Git init prompt. Global lock. |
| `codefission/registry.py` | New. Project registry with auto-naming (git remote / basename) and editable `custom_name`. |
| `codefission/config.py` | Remove `PROJECT_PATH`/`PROJECT_DIR` globals. Keep `DATA_DIR`. |
| `codefission/db.py` | Multi-DB support: `get_db(project_path)` with connection cache. |
| `codefission/handlers.py` | Per-connection project context. New: `handle_open_project`, `handle_list_projects`. Remove tree CRUD handlers. |
| `codefission/events.py` | Add project messages (`OPEN_PROJECT`, `LIST_PROJECTS`, `RENAME_PROJECT`). Remove/simplify tree messages. |
| `codefission/services/orchestrator.py` | Project-scoped. Auto-create single tree. `update_tree_base()`. |
| `codefission/services/workspace_service.py` | All functions take `project_path` param. Protective git ref. `check_staleness()`. |
| `codefission/services/tree_service.py` | All functions take DB connection param (multi-project). |
| `frontend/src/store.ts` | Add `projects`, `sidebarOpen`, `mergeResult`. Simplify tree state. |
| `frontend/src/ws.ts` | Project messages. Remove tree CRUD messages. Auto-open project from URL. |
| `frontend/src/components/TreeList.tsx` | Rename to `ProjectList.tsx`. Show projects, not trees. |
| `frontend/src/components/App.tsx` | Sidebar starts closed. Canvas-first layout. |
| `frontend/src/components/TreeNode.tsx` | Staleness banner. Merge result feedback. |

---

## Verification

1. `fission` in a git project → server starts, browser opens, canvas shows tree, **sidebar closed**
2. `fission` in another project (server already running) → browser opens to same server with new project loaded, sidebar closed
3. `fission` with no project / home dir → server starts, **sidebar open** showing project list
4. `fission` in non-git dir → prompts "Initialize git repo? [Y/n]"
5. Sidebar shows project names: GitHub remote path (e.g., `user/repo`) or local basename
6. Double-click project name in sidebar → inline rename, persists across sessions
7. Click different project in sidebar → canvas switches to that project's tree
8. Advance main externally → root node shows "3 new commits" + "Update base" button
9. Merge a leaf node → inline success/conflict feedback
10. Kill server, re-run `fission` → stale lock detected, starts fresh
11. `make test` passes
