# CodeFission Backend Rewrite — Test Plan

Organized by phase from `backend-rewrite-plan.md`. Each section lists what to test, the test style (unit / integration / E2E), and whether the test uses mocks or real git/DB.

All tests use the existing `conftest.py` pattern: `tmp_project` (temp git repo), `tmp_db` (temp SQLite), `monkeypatch` for config isolation.

---

## Phase 1: Extract Model from handlers

### 1A. Orchestrator — chat streaming as async generator

The big refactor: `_run_chat` moves from handlers.py to Orchestrator. The Orchestrator's `chat()` method becomes an async generator yielding domain events.

**Test file**: `tests/test_orchestrator_chat.py`

```
TestOrchestratorChat:
  test_chat_yields_node_created_first
    - call orch.chat(root.id, "hello") with mocked stream_chat
    - first yielded event is ChatNodeCreated with a valid node
    - node exists in DB with status="active"

  test_chat_yields_text_deltas
    - mock stream_chat to yield TextDelta("hello"), TextDelta(" world")
    - collect events from orch.chat()
    - assert TextDelta events appear in order

  test_chat_yields_tool_events
    - mock stream_chat to yield ToolStart(name="Bash"), ToolEnd(result="ok")
    - assert ToolStart and ToolEnd appear in collected events

  test_chat_yields_completed_last
    - mock stream_chat to yield text + TurnComplete
    - last event is ChatCompleted with result containing git_commit, files_changed

  test_chat_saves_response_on_completion
    - mock stream_chat, consume all events
    - node in DB has status="done", assistant_response set, git_commit set

  test_chat_records_provider_and_model_on_node
    - mock stream_chat with provider="claude", model="claude-opus-4-6"
    - after completion, node in DB has provider="claude", model="claude-opus-4-6"

  test_chat_cancellation_yields_error
    - mock stream_chat, cancel mid-stream
    - assert node status="error", assistant_response contains "[Cancelled by user]"

  test_chat_exception_yields_error
    - mock stream_chat to raise Exception
    - assert node status="error"

  test_chat_no_file_changes_still_completes
    - mock stream_chat with no file edits
    - ChatCompleted has files_changed=0
    - node still gets a git_commit (parent's commit)
```

Style: **integration** — real DB + real git repo, mocked `stream_chat` (no actual AI subprocess).

### 1B. Orchestrator — operations extracted from handlers

Logic that was inline in handlers.py, now in Orchestrator methods.

**Test file**: `tests/test_orchestrator_ops.py`

```
TestDeleteNode:
  test_deletes_leaf_node
    - create tree → branch → delete the branch node
    - node gone from DB, parent's children_ids updated

  test_deletes_subtree
    - create tree → node A → node B (child of A)
    - delete A → both A and B gone

  test_cannot_delete_root
    - attempt to delete root → raises ValueError

  test_cannot_delete_streaming_node
    - set node status="active"
    - attempt to delete → raises ValueError

  test_cleans_up_settings_on_delete
    - set expanded_nodes with the node ID
    - delete node
    - expanded_nodes no longer contains the ID

  test_cleans_up_worktree_on_delete
    - create node with worktree (via prepare_chat)
    - delete node
    - worktree directory removed

TestOpenRepo:
  test_finds_existing_tree
    - create tree for repo_id + base_commit
    - open_repo with same repo_id + base_commit → returns existing tree

  test_creates_new_tree_if_none_exists
    - open_repo on fresh repo → creates tree, returns it

  test_updates_repo_path_if_moved
    - create tree with repo_path="/old/path"
    - open_repo from "/new/path" same repo_id → updates repo_path

TestUpdateBase:
  test_updates_base_commit
    - create tree, make a new commit on the branch
    - update_base with new commit → tree.base_commit updated

  test_rejects_invalid_commit
    - update_base with nonexistent SHA → raises error

  test_rejects_update_after_children_created
    - create tree, branch from root, create child
    - update_base → raises error (children exist)

TestSettings:
  test_update_global_settings
    - update provider, model, max_turns
    - get_global_defaults returns new values

  test_update_tree_settings
    - update tree provider, model, skill
    - get_tree returns updated values

  test_tree_settings_override_global
    - set global model="opus", tree model="sonnet"
    - resolve_tree_settings returns "sonnet"

TestFileOperations:
  test_list_files_from_worktree
    - create worktree with files
    - list_files returns file list

  test_list_files_from_commit_when_no_worktree
    - complete chat (worktree removed), node has git_commit
    - list_files falls back to git commit → returns files

  test_get_diff_shows_changes
    - create file in child worktree
    - get_diff returns unified diff

  test_read_file_content
    - create file in worktree
    - read_file returns content
```

Style: **integration** — real DB + real git.

### 1C. Global state removal

**Test file**: `tests/test_concurrency.py`

```
TestNoConcurrencyRaces:
  test_two_chats_different_repos_no_race
    - create two trees in different tmp repos
    - start prepare_chat on both concurrently (asyncio.gather)
    - each resolves the correct workspace / repo_path
    - (verifies repo_path is passed explicitly, not via global)

  test_orchestrator_takes_repo_path_param
    - call orch.create_tree with explicit repo_path
    - verify tree.repo_path matches, no global state set
```

Style: **integration**.

### 1D. Sandbox deletion

**Test file**: no new tests — just verify existing `test_sandbox.py` is deleted and no imports remain.

```
TestSandboxRemoved:
  test_no_sandbox_imports
    - grep for "sandbox" in all .py files under codefission/
    - only matches should be in tests or comments, not active imports

  test_no_sandbox_files_exist
    - assert sandbox.py, _sandbox_linux.py, _sandbox_darwin.py do not exist
```

Style: **unit** (file existence check).

---

## Phase 2: Audit log + REST API

### 2A. Audit log

**Test file**: `tests/test_actions.py`

```
TestActionLog:
  test_record_returns_action_with_seq
    - record("create_tree", tree_id, None, {"name": "T"})
    - returned Action has id, seq, ts, kind="create_tree"

  test_seq_auto_increments
    - record two actions
    - second action's seq > first action's seq

  test_list_actions_by_tree
    - record actions for tree_1 and tree_2
    - list_actions(tree_1) returns only tree_1's actions

  test_list_actions_ordered_by_seq
    - record 5 actions
    - list_actions returns them in seq order

  test_list_actions_limit
    - record 10 actions
    - list_actions(limit=3) returns 3

  test_update_result
    - record action with empty result
    - update_result with {cost_usd: 0.05}
    - fetch action → result has cost_usd

  test_source_field_default_gui
    - record without source → source="gui"

  test_source_field_cli
    - record(source="cli") → source="cli"

  test_action_params_stored_as_json
    - record with params={"key": "value", "nested": [1,2,3]}
    - fetch → params decoded correctly

  test_replay_returns_all_actions
    - record 10 actions for a tree
    - replay(tree_id) returns all 10 in order
```

Style: **integration** — real DB.

### 2B. Orchestrator records actions

**Test file**: `tests/test_orchestrator_actions.py`

```
TestOrchestratorAuditLog:
  test_create_tree_records_action
    - create tree
    - list_actions → contains kind="create_tree" with params.name

  test_branch_records_action
    - branch from root
    - list_actions → contains kind="branch" with result.node_id

  test_chat_records_action_with_result
    - run mocked chat to completion
    - list_actions → contains kind="chat" with params.message, result.cost_usd

  test_cancel_chat_records_action
    - cancel a chat
    - list_actions → contains kind="cancel_chat"

  test_delete_node_records_action
    - delete node
    - list_actions → contains kind="delete_node" with result.deleted_count

  test_settings_change_records_action
    - update global setting
    - list_actions → contains kind="set_global" with params.key, params.value

  test_action_source_matches_caller
    - (verify the source parameter is passed through correctly — deferred
      until REST routes exist, since source is set by the presenter)
```

Style: **integration** — real DB + real git, mocked stream_chat.

### 2C. REST API (CLI Presenter)

**Test file**: `tests/test_rest_api.py`

Uses FastAPI's `TestClient` (httpx-based, async).

```
TestTreeRoutes:
  test_post_trees_creates_tree
    - POST /api/trees {"name": "T", "base_branch": "main"}
    - 201, response has tree_id

  test_get_trees_lists_all
    - create 3 trees
    - GET /api/trees → 200, 3 trees in response

  test_delete_tree
    - create tree
    - DELETE /api/trees/:id → 200
    - GET /api/trees → 0 trees

  test_patch_tree_updates_settings
    - create tree
    - PATCH /api/trees/:id {"provider": "codex", "model": "o4-mini"}
    - GET tree → provider="codex", model="o4-mini"

TestNodeRoutes:
  test_post_branch_creates_child
    - POST /api/trees/:id/nodes/:root_id/branch {"label": "try alt"}
    - 201, response has node_id, parent_id=root_id

  test_delete_node_removes_subtree
    - create tree → branch → child
    - DELETE /api/trees/:id/nodes/:child_id → 200
    - GET parent → children_ids does not include child

  test_get_node_returns_details
    - create tree + chat
    - GET /api/trees/:id/nodes/:id → 200, has user_message, status, git_commit

  test_get_node_files
    - create tree + chat with file
    - GET /api/trees/:id/nodes/:id/files → file list

  test_get_node_diff
    - create tree + chat with file change
    - GET /api/trees/:id/nodes/:id/diff → unified diff string

TestChatRoutes:
  test_post_chat_streams_sse
    - POST /api/trees/:id/nodes/:id/chat {"message": "hello"}
    - response is SSE stream (text/event-stream)
    - events include ChatNodeCreated, TextDelta, ChatCompleted

  test_post_cancel
    - start chat, then POST /api/trees/:id/nodes/:id/cancel
    - node status → "error"

TestSettingsRoutes:
  test_get_settings
    - GET /api/settings → 200, has provider, model, max_turns defaults

  test_patch_settings
    - PATCH /api/settings {"default_provider": "codex"}
    - GET /api/settings → provider="codex"

TestProviderRoutes:
  test_get_providers
    - GET /api/providers → 200, list of providers with install/auth status
    - (uses agentbridge.discover — may need mock if CLI tools not installed)

TestAuditLogRoutes:
  test_get_log
    - create tree, branch, chat
    - GET /api/trees/:id/log → actions in order

TestCrossPresenterNotification:
  test_rest_mutation_emits_eventbus
    - subscribe to EventBus "tree_created"
    - POST /api/trees
    - assert event was emitted (WS Presenter would have received it)
```

Style: **integration** — FastAPI TestClient, real DB, mocked stream_chat.

---

## Phase 3: CLI View

### 3A. CLI commands

**Test file**: `tests/test_cli.py`

Uses Click's `CliRunner` with a mock HTTP server (or monkeypatched `httpx`).

```
TestCliRequiresServer:
  test_commands_fail_without_server
    - run "fission tree ls" without server running
    - exit code 1, stderr contains "Server not running"

  test_fission_serve_starts_server
    - run "fission serve" in background
    - health check succeeds

TestTreeCommands:
  test_tree_ls_empty
    - mock GET /api/trees → []
    - "fission tree ls" → "No trees"

  test_tree_ls_shows_trees
    - mock GET /api/trees → [{"name": "T1"}, {"name": "T2"}]
    - output contains "T1" and "T2"

  test_tree_new
    - mock POST /api/trees → {"tree_id": "abc"}
    - "fission tree new 'Add auth'" → output contains "abc"

  test_tree_new_from_node
    - mock POST /api/trees with from_node_id
    - "fission tree new 'Alt' --from abc123" → success

  test_tree_rm
    - mock DELETE /api/trees/:id
    - "fission tree rm abc" → success

  test_tree_use
    - "fission tree use abc"
    - cli_state.json updated with tree_id="abc"

TestChatCommand:
  test_chat_streams_output
    - mock POST /api/.../chat → SSE stream with TextDelta events
    - "fission chat 'hello'" → prints streamed text to stdout

  test_chat_shows_cost_on_completion
    - mock SSE with ChatCompleted(cost_usd=0.05)
    - output contains "$0.05"

  test_chat_with_quote
    - "fission chat -q src/main.py 'review this'"
    - request body includes file quote

  test_chat_with_model_override
    - "fission chat --model claude-sonnet-4-6 'hello'"
    - request body includes model override

TestSettingsCommands:
  test_set_shows_all
    - mock GET /api/settings
    - "fission set" → displays global + tree settings

  test_set_provider
    - "fission set provider codex"
    - mock PATCH /api/settings called with {"default_provider": "codex"}

  test_set_tree_model
    - "fission set --tree model o4-mini"
    - mock PATCH /api/trees/:id called with {"model": "o4-mini"}

  test_set_reset
    - "fission set --reset provider"
    - mock PATCH /api/settings called with {"default_provider": null}

TestNodeCommands:
  test_ls_shows_tree_structure
    - mock tree with 3-level structure
    - "fission ls" → indented tree output

  test_select_updates_state
    - "fission select abc"
    - cli_state.json updated with node_id="abc"

  test_show_displays_conversation
    - mock node with user_message + assistant_response
    - "fission show abc" → shows both

TestLoginCommand:
  test_login_shows_status
    - mock agentbridge.discover_sync
    - "fission login" → shows provider status

TestLogCommand:
  test_log_shows_actions
    - mock GET /api/trees/:id/log → actions
    - "fission log" → formatted action list

  test_log_json
    - "fission log --json" → valid JSON array
```

Style: **unit** — Click CliRunner, mocked HTTP.

---

## Phase 4: Provider-agnostic chat

### 4A. Session continuity

**Test file**: `tests/test_session_continuity.py`

```
TestResolveSessionContinuity:
  test_root_parent_returns_fresh_start
    - parent has no user_message (root)
    - returns (None, False, None)

  test_same_provider_returns_fork
    - parent.provider="claude", parent.session_id="sess_abc"
    - new_provider="claude"
    - returns ("sess_abc", True, None)

  test_same_provider_different_model_still_forks
    - parent.provider="claude", parent.model="opus", parent.session_id="sess_abc"
    - new_provider="claude" (model will be "sonnet")
    - returns ("sess_abc", True, None) — session fork works across models

  test_different_provider_returns_context_transfer
    - parent.provider="claude", parent.session_id="sess_abc"
    - new_provider="codex"
    - returns (None, False, "<context text>")

  test_no_session_id_returns_context_transfer
    - parent.provider="claude", parent.session_id=None
    - new_provider="claude"
    - returns (None, False, "<context text>")

  test_empty_parent_message_returns_fresh
    - parent.user_message="" (empty branch, no chat yet)
    - returns (None, False, None)

TestBuildContextFromAncestors:
  test_single_ancestor
    - parent has user_message="hello", assistant_response="hi"
    - context contains "hello" and "hi"

  test_ancestor_chain_in_order
    - grandparent → parent → (current)
    - context lists grandparent conversation first, then parent

  test_skips_empty_messages
    - root (empty) → parent (has message)
    - context only includes parent's conversation

  test_uses_agentbridge_format
    - builds ConversationHistory, passes to format_history_as_context
    - result starts with "[Context from previous"

  test_truncation_on_long_history
    - 20 ancestors with long responses
    - context is truncated to reasonable size
```

Style: **unit** — mock DB calls, real agentbridge formatting.

### 4B. Provider/model on nodes

**Test file**: `tests/test_node_provider.py`

```
TestNodeProviderModel:
  test_chat_saves_provider_on_node
    - mock chat with provider="claude"
    - node.provider == "claude" after completion

  test_chat_saves_model_on_node
    - mock chat with model="claude-opus-4-6"
    - node.model == "claude-opus-4-6" after completion

  test_root_has_null_provider
    - create tree
    - root.provider is None

  test_mid_tree_provider_switch
    - n2 uses claude, n3 uses codex, n4 uses claude
    - each node records the correct provider

  test_provider_column_migration
    - init DB, verify nodes table has provider and model columns
```

Style: **integration** — real DB.

### 4C. Provider discovery via agentbridge

**Test file**: `tests/test_provider_discovery.py`

```
TestProviderDiscovery:
  test_discover_returns_providers
    - mock agentbridge.discover
    - verify response shape matches WS.PROVIDERS format

  test_providers_directory_deleted
    - assert codefission/providers/ does not exist

  test_no_list_providers_imports
    - grep for "from providers import" → no matches
```

Style: **unit**.

---

## Phase 5: Cleanup

### 5A. Renames

**Test file**: `tests/test_imports.py`

```
TestRenames:
  test_trees_importable
    - from services.trees import create_tree, get_tree, get_node → works

  test_chat_importable
    - from services.chat import stream_chat → works

  test_workspace_importable
    - from services.workspace import create_worktree → works

  test_no_old_names_imported
    - grep for "tree_service", "chat_service", "workspace_service" in non-test .py files → 0 matches
```

Style: **unit**.

---

## Cross-cutting: WS Presenter (handlers.py)

Existing WS behavior must not break during refactoring. These are regression tests.

**Test file**: `tests/test_ws_presenter.py`

Uses FastAPI's `WebSocketTestClient`.

```
TestWSPresenterRegression:
  test_create_tree_via_ws
    - send {"type": "create_tree", "name": "T", "base_branch": "main"}
    - receive {"type": "tree_created", tree: {...}}

  test_load_tree_via_ws
    - create tree, send {"type": "load_tree", "tree_id": id}
    - receive {"type": "tree_loaded", tree: {...}, nodes: [...]}

  test_chat_via_ws_streams_chunks
    - send {"type": "chat", "node_id": root, "content": "hello"}
    - receive node_created, then chunk events, then done

  test_cancel_via_ws
    - start chat, send {"type": "cancel", "node_id": id}
    - receive error event

  test_settings_via_ws
    - send {"type": "get_settings"}
    - receive {"type": "settings", global_defaults: {...}}

  test_delete_node_via_ws
    - create tree + branch, send {"type": "delete_node", "node_id": id}
    - receive {"type": "nodes_deleted", deleted_ids: [...]}

TestCrossPresenterSync:
  test_rest_create_tree_notifies_ws
    - connect WS client, subscribe to events
    - POST /api/trees via REST
    - WS client receives tree_created event

  test_rest_chat_notifies_ws
    - connect WS client
    - POST /api/.../chat via REST
    - WS client receives node_created + chunk + done events

  test_rest_delete_notifies_ws
    - connect WS client, create tree
    - DELETE /api/trees/:id via REST
    - WS client receives tree_deleted event

  test_rest_settings_notifies_ws
    - connect WS client
    - PATCH /api/settings via REST
    - WS client receives settings event
```

Style: **integration** — FastAPI test clients (HTTP + WS), real DB, mocked stream_chat.

---

## Cross-cutting: EventBus

**Test file**: `tests/test_eventbus.py`

```
TestEventBus:
  test_emit_calls_subscriber
    - bus.on("tree_created", callback)
    - bus.emit("tree_created", tree=...)
    - callback was called with tree

  test_multiple_subscribers
    - register 3 callbacks for same event
    - emit → all 3 called

  test_off_removes_subscriber
    - register callback, then bus.off
    - emit → callback NOT called

  test_emit_unknown_event_is_noop
    - emit event with no subscribers → no error

  test_subscriber_error_does_not_block_others
    - register callback_a (raises), callback_b (succeeds)
    - emit → callback_b still called
```

Style: **unit**.

---

## Cross-cutting: Plant tree from node

**Test file**: `tests/test_plant_tree.py`

```
TestPlantFromNode:
  test_plant_from_node_creates_tree_at_commit
    - create tree A, chat on root → node n2 with git_commit
    - plant new tree B from n2
    - tree B's base_commit == n2's git_commit

  test_plant_from_node_same_repo
    - plant from n2
    - new tree's repo_path matches original tree's repo_path

  test_plant_from_node_provenance
    - plant from n2
    - new tree's forked_from == {tree_id: A, node_id: n2}

  test_plant_from_node_root_has_correct_commit
    - plant from n2 (commit=def456)
    - new tree's root node has git_commit=def456

  test_plant_from_nonexistent_node_fails
    - plant from "nonexistent" → error
```

Style: **integration** — real DB + real git.

---

## E2E: Full workflow via CLI

The final validation. These tests run the actual `fission` CLI commands against a real server with a real DB and real git repos. The only mock is the AI provider (agentbridge `stream_chat` returns canned responses).

**Test file**: `tests/test_e2e_cli.py`

Each test starts a real `fission serve` in a subprocess (or in-process via TestClient), then runs CLI commands via `CliRunner` or `subprocess.run`.

```
TestE2EBasicWorkflow:
  test_init_to_chat_to_log
    - fission init (in tmp git repo)
    - fission serve (background)
    - fission tree new "E2E test"
    - fission tree ls → shows "E2E test"
    - fission chat "hello"
      → output streams text
      → output shows cost
    - fission ls → shows root + child node
    - fission show <child_id> → shows "hello" + response
    - fission log → shows create_tree + chat actions
    - fission log --json → valid JSON

TestE2EBranching:
  test_branch_and_diverge
    - fission tree new "Branch test"
    - fission chat "create app.py"
    - fission branch "try alt"
    - fission chat "create alt.py"
    - fission files <node_a> → has app.py
    - fission files <node_b> → has alt.py, has app.py (inherited)
    - fission diff <node_b> → shows alt.py added

TestE2EProviderSwitch:
  test_switch_provider_mid_tree
    - fission tree new "Provider test"
    - fission chat "hello" (uses default: claude mock)
    - fission tree set provider codex
    - fission chat "continue" (uses codex mock)
      → verify context transfer happened (prompt includes prior conversation)
    - fission tree set provider claude
    - fission chat "back to claude"
      → verify context transfer includes both prior turns
    - fission show <last_node> → provider=claude
    - fission show <middle_node> → provider=codex

TestE2EModelSwitch:
  test_switch_model_same_provider
    - fission tree new "Model test"
    - fission chat "hello" (opus)
    - fission tree set model claude-sonnet-4-6
    - fission chat "continue" (sonnet)
      → verify session fork (not context transfer) since same provider
    - fission show <last_node> → model=claude-sonnet-4-6

TestE2ESettings:
  test_settings_lifecycle
    - fission set → shows defaults
    - fission set provider codex → success
    - fission set → provider=codex
    - fission set --tree model o4-mini → success
    - fission set → shows tree override + global
    - fission set --tree --reset model → success
    - fission set → model inherited from global

TestE2EPlantFromNode:
  test_plant_tree_from_node
    - fission tree new "Tree A"
    - fission chat "build something"
    - note the child node ID
    - fission tree new "Tree B" --from <child_id>
    - fission tree use <tree_b_id>
    - fission ls → root node exists
    - fission files → same files as the source node

TestE2EDeleteAndCancel:
  test_delete_node
    - fission tree new "Delete test"
    - fission chat "hello"
    - fission branch "to delete"
    - fission rm <branch_id>
    - fission ls → branch node gone

  test_cancel_chat
    - fission tree new "Cancel test"
    - start "fission chat 'long task'" (mock with slow stream)
    - send interrupt / cancel
    - fission show <node_id> → status=error, response contains "[Cancelled"
    - fission chat "continue" → new child from cancelled node

TestE2ENotes:
  test_note_lifecycle
    - fission tree new "Notes test"
    - fission note add "Remember this"
    - fission note ls → shows "Remember this"
    - fission note edit <id> "Updated"
    - fission note ls → shows "Updated"
    - fission note rm <id>
    - fission note ls → empty

TestE2EMerge:
  test_merge_to_branch
    - fission tree new "Merge test" --branch main
    - fission chat "create feature.py"
    - fission merge <node_id> main
    - verify feature.py exists on main branch (via git)

TestE2ECrossViewSync:
  test_cli_action_visible_in_ws
    - start server
    - connect WS client (simulated GUI)
    - run "fission tree new 'Sync test'" via CLI
    - WS client received tree_created event
    - run "fission chat 'hello'" via CLI
    - WS client received node_created + chunk + done events

  test_cli_reads_ui_changes
    - start server
    - create tree via WS (simulated GUI)
    - "fission tree ls" → shows the tree created via GUI
```

Style: **E2E** — real server, real CLI, real DB, real git. Only AI is mocked.

These are the final gate — if these pass, the rewrite is correct.

---

## Summary: test count by phase

| Phase | File | Approx tests | Style |
|-------|------|-------------|-------|
| 1A | test_orchestrator_chat.py | 9 | integration |
| 1B | test_orchestrator_ops.py | 20 | integration |
| 1C | test_concurrency.py | 2 | integration |
| 1D | (sandbox deletion check) | 2 | unit |
| 2A | test_actions.py | 10 | integration |
| 2B | test_orchestrator_actions.py | 7 | integration |
| 2C | test_rest_api.py | 20 | integration |
| 3A | test_cli.py | 22 | unit |
| 4A | test_session_continuity.py | 10 | unit |
| 4B | test_node_provider.py | 5 | integration |
| 4C | test_provider_discovery.py | 3 | unit |
| 5A | test_imports.py | 4 | unit |
| — | test_ws_presenter.py | 10 | integration |
| — | test_eventbus.py | 5 | integration |
| — | test_plant_tree.py | 5 | integration |
| **E2E** | **test_e2e_cli.py** | **20** | **E2E** |
| | | **~154 new tests** | |

Plus ~30 existing orchestrator tests that should still pass after refactoring (regression).

### Test pyramid

```
        /  E2E  \          ~20 tests — full CLI → server → DB → git
       / ───────── \
      / Integration  \     ~100 tests — Orchestrator + DB + git, mocked AI
     / ─────────────── \
    /      Unit          \  ~34 tests — pure logic, no DB/git
   /─────────────────────── \
```

E2E tests are the final gate. If they pass, the rewrite is correct from the user's perspective.

---

## Test infrastructure notes

**Mocking stream_chat**: Most tests mock the agentbridge layer. The mock yields a fixed sequence of `BridgeEvent` objects (SessionInit, TextDelta, TurnComplete). This avoids needing Claude/Codex CLI installed in CI.

```python
async def mock_stream_chat(*args, **kwargs):
    yield SessionInit(session_id="test-session")
    yield TextDelta(text="Hello from mock")
    yield TurnComplete(session_id="test-session", cost_usd=0.01)
```

**Real git repos**: Tests that verify worktree, commit, diff, and file operations use real `git init` in tmp dirs (existing pattern from `test_orchestrator.py`).

**FastAPI test clients**: REST and WS tests use `httpx.AsyncClient` with `app` (for REST) and `WebSocketTestClient` (for WS). Both share the same in-process server — EventBus cross-notification works naturally.

**No real AI calls**: No test spawns an actual Claude or Codex subprocess. All AI interaction is mocked at the agentbridge boundary.
