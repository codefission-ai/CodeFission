# Known Bugs — CodeFission

Audit date: 2026-03-04

---

## Critical

### BUG-1: File descriptor leak in `stream_chat`

**File:** `backend/services/chat_service.py:175`

```python
debug_stderr=open(os.devnull, "w"),
```

Every call to `stream_chat` opens `/dev/null` for writing but never closes it. After sustained use, the process will exhaust the file descriptor limit and crash with "Too many open files".

**Fix:** Open once at module level or use a context manager.

---

### BUG-2: Race condition — concurrent chats on the same node corrupt state

**File:** `backend/main.py:191, 260, 290, 292-293`

If a user sends two rapid chat messages for the same `node_id`:
- `_streams[nid]` is overwritten, resetting accumulated text
- `tasks[node_id]` is overwritten, losing the reference to the first task
- On WebSocket disconnect, only the second task is cancelled; the first runs as an orphan

**Fix:** Reject or queue chat requests if a node is already streaming. Check `nid in _streams` before starting.

---

### BUG-3: Auto-created child nodes never get `git_branch` set

**File:** `backend/main.py:157-160`

When `_run_chat` auto-creates a child node (because the current node already has `assistant_response`), it calls `create_child_node` and then proceeds to `ensure_worktree`. The worktree gets created with branch `ct-{node_id}`, but `update_node(node.id, git_branch=branch_name)` is never called for the auto-created child. Compare with the explicit `handle_branch` path (line 127-128) which does set `git_branch`.

Result: auto-created child nodes have `git_branch=None` permanently, causing the system prompt to omit branch context.

**Fix:** After `ensure_worktree` succeeds for the auto-child, call `update_node(nid, git_branch=f"ct-{nid}")`.

---

### BUG-4: `handle_set_repo` + idempotent `setup_repo` can leave corrupted workspace

**File:** `backend/main.py:305-309`

`shutil.rmtree(root_dir, ignore_errors=True)` silently fails if files are locked. If `.git` survives partial deletion, the subsequent `setup_repo()` sees `.git` exists and returns early (idempotent check), leaving a corrupted workspace.

Additionally, child worktrees are not cleaned up when the root repo is deleted — they reference the old repo via relative `.git` file paths and become permanently broken.

**Fix:** Use `shutil.rmtree` without `ignore_errors`, handle the exception. Also clean up all child worktrees when changing repo mode.

---

### BUG-5: `list_trees` does not populate `root_node_id`

**File:** `backend/services/tree_service.py:37-46`

`list_trees()` constructs `Tree` objects without querying for `root_node_id`. Every tree in the list has `root_node_id=None`. Currently benign because the frontend doesn't access it from the tree list, but a data consistency issue that will bite if any code path relies on it.

**Fix:** Add a second query (or JOIN) to populate `root_node_id` in `list_trees`.

---

## Medium

### BUG-6: `_run_chat` exception handler tries to send on closed WebSocket

**File:** `backend/main.py:280-287`

If the WebSocket disconnects during streaming, `await send(WS.CHUNK, ...)` raises. The `except Exception` catches it, then tries `await send(WS.ERROR, ...)` on the already-closed WebSocket — raising again. This second exception is unhandled (it's in a `create_task`).

Also, `update_node(nid, status="error")` at line 285 incorrectly marks nodes as "error" when the real issue was a client disconnect.

**Fix:** Wrap the error `send()` in a try/except. Distinguish between chat errors and disconnect errors.

---

### BUG-7: `handle_branch` falls back to `"HEAD"` which resolves to wrong commit

**File:** `backend/main.py:125`

```python
parent.git_commit or "HEAD"
```

If the parent node has no `git_commit`, `"HEAD"` resolves relative to the **main repo**, which may have advanced. The child worktree is created from the wrong commit.

**Fix:** Resolve HEAD from the parent's worktree, not the main repo.

---

### BUG-8: `create_worktree` fails if branch name already exists

**File:** `backend/services/workspace_service.py:98-110`

If a worktree was previously deleted but the git branch `ct-{node_id}` still exists, `git worktree add -b ct-{node_id}` fails because the branch already exists.

**Fix:** Check if the branch exists first; if so, use `git worktree add` without `-b`, or delete the stale branch first.

---

### BUG-9: Frontend ERROR handler doesn't handle missing `node_id`

**File:** `frontend/src/ws.ts:131-134`

Some backend error paths send `WS.ERROR` without `node_id` (e.g., `handle_set_repo` line 300, `handle_get_file_content` line 377). The frontend calls `actions.setNodeStatus(undefined, "error")`, creating a spurious `"undefined"` key in the store. The actual error message is never displayed to the user.

**Fix:** Check `data.node_id` before updating node status. Display `data.error` as a toast/alert.

---

### BUG-10: `RepoSelector` gets permanently stuck after failed setup

**File:** `frontend/src/components/TreeNode.tsx:46, 58`

`setSetting(true)` is called on submit, but never reset to `false`. On success, the component switches to `RepoBadge` (so it doesn't matter). On failure, the button stays disabled showing "..." with no way to retry. The user must reload the page.

**Fix:** Listen for `WS.ERROR` or `WS.TREE_UPDATED` to reset `setting` state.

---

### BUG-11: `cleanup_tree_workspace` is synchronous and blocks the event loop

**File:** `backend/services/workspace_service.py:237-241`, `backend/main.py:108`

`shutil.rmtree` on a large workspace blocks the entire async event loop, freezing all WebSocket connections.

**Fix:** Run in `asyncio.to_thread(shutil.rmtree, ...)` or use `aioshutil`.

---

### BUG-12: `delete_tree` calls `cleanup_tree_workspace` twice

**File:** `backend/main.py:108-109` and `backend/services/tree_service.py:199`

`handle_delete_tree` calls `cleanup_tree_workspace`, then `delete_tree` calls it again internally. Harmless but redundant.

**Fix:** Remove the call from one location. Prefer keeping it in `delete_tree` for encapsulation.

---

### BUG-13: `_pump_stream` exception re-raise loses original traceback

**File:** `backend/main.py:209-222`

```python
except Exception as exc:
    await event_queue.put(exc)
# ...
if isinstance(event, Exception):
    raise event  # traceback points here, not the original site
```

`raise event` creates a new traceback from the re-raise point. The `traceback.print_exc()` at line 282 shows the wrong location.

**Fix:** Use `raise event.with_traceback(event.__traceback__)` or store `sys.exc_info()` in the queue.

---

### BUG-14: DB migration default `'none'` doesn't match new model default `'new'`

**File:** `backend/db.py:59`

```python
await db.execute("ALTER TABLE trees ADD COLUMN repo_mode TEXT NOT NULL DEFAULT 'none'")
```

The migration uses `DEFAULT 'none'` but the model now defaults to `"new"`. Existing databases migrated from older versions will have trees with `repo_mode='none'`, but the code no longer handles `"none"` mode anywhere.

**Fix:** Add a data migration: `UPDATE trees SET repo_mode='new' WHERE repo_mode='none'`.

---


## Low

### BUG-15: `_silence_asyncgen_gc` may silence legitimate errors

**File:** `backend/main.py:51-56`

Any `RuntimeError` containing "cancel scope" is silenced, not just SDK cleanup errors.

---

### BUG-16: Frontend drops messages silently during reconnection

**File:** `frontend/src/ws.ts:57-59`

`send()` silently drops messages if the WebSocket is not open. User actions during reconnection are lost with no feedback.

---

### BUG-17: Frontend reconnect doesn't re-request current tree state

**File:** `frontend/src/ws.ts:40-55`

On reconnect, `onopen` sends `LIST_TREES` but does not re-load the current tree's nodes. Streaming state becomes stale.

---

### BUG-18: Path traversal check has theoretical prefix bypass

**File:** `backend/services/workspace_service.py:224-226`

`startswith` string check can be bypassed if worktree path is a prefix of another directory (e.g., `/data` vs `/data-secret`). Unlikely with UUID-based paths.

**Fix:** Append `/` to the base path before comparison: `str(resolved).startswith(str(worktree_path.resolve()) + "/")`.

---

### BUG-19: `_run_chat` orphans tasks when `nid` is rebound to auto-child

**File:** `backend/main.py:160, 292-293`

When `nid` is rebound to a child ID, the task is stored under the original `node_id`. If another chat arrives for the same original `node_id`, the old task reference is overwritten. Combined with BUG-2.
