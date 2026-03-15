# Frontend UX Plan

## Sidebar: Projects & Trees

- Left panel shows **all projects** (one per git repo) with **trees** nested under each
- Projects and trees ranked by recency (most recently used at top)
- Server is project-agnostic — serves all projects. `fission` from CLI just selects the most recent tree for that repo
- **Project creation**:
  - Click "new project" button in sidebar → assigns an empty folder by default
  - User can change the folder to a local git path or a GitHub URL
  - If the folder is not a git repo, warn and offer to `git init`
  - From CLI: `fission` in a git repo creates/finds the project and opens its most recent tree
  - Project name auto-detected from folder name or remote repo name, editable by user
- **Tree creation**:
  - Click "+" next to project name in sidebar
  - Or right-click an edit-node → "New tree from here" (inherits system instructions)
  - User is asked to name the tree (auto-summarized later from first message)
  - Defaults to current branch HEAD; user can change branch and commit
- **Switching projects**: click a project in sidebar → loads its most recent tree on canvas. No server restart needed.

**Backend impact**: server needs to stop binding to one repo on launch. Remove `CODEFISSION_REPO_PATH` env var approach — the server serves all projects in the DB. `fission .` just tells the server "focus this repo" via the WS `open_repo` message (already exists).

## Node Types: Chat vs Edit

- **Chat node**: AI responded but didn't change files → `git_commit == parent.git_commit`
- **Edit node**: AI modified files, a new commit was created → `git_commit != parent.git_commit`
- Edit nodes get a distinct visual (different border color, small icon, or background tint)
- No backend changes — data already there, frontend checks `node.git_commit !== parent.git_commit`

## Remove Chat Panel

- Delete the right-side chat panel entirely — it's unused
- All interaction happens through the canvas tree nodes
- Long responses: click a node to expand in the existing NodeModal
- Delete `ChatPanel.tsx` and all references

## Node Collapsing

- Replace the current expand/collapse subtree feature with simple click-to-toggle on individual nodes
- Large trees: ReactFlow handles virtualization — only visible nodes render, so canvas performance is fine even with 100+ nodes. No custom collapse logic needed.
- Remove `expanded_nodes` and `collapsed_subtrees` DB settings + handler code
- If a subtree is unwanted, the user deletes it

**Backend cleanup**: remove `handle_set_expanded`, `handle_set_subtree_collapsed`, and the settings they write to.

## Tool Call Display

- During streaming: show only the latest 2-3 tool calls
- Auto-collapse older tool calls into a summary line ("12 tool calls")
- User can click to expand collapsed tools for inspection
- Keep tool calls in the response text — just collapse them visually

## Unread Indicator

- When a chat completes, the node gets a visual indicator (glow, colored dot)
- Indicator disappears when the user clicks/focuses the node
- Frontend-only state for now (no DB persistence)

## Notes: Pin to Node

- Free-floating notes can optionally be pinned to a specific node
- Pinned notes stay visually near their node
- Adds `pinned_to_node_id` to notes JSON
- Behavior when pinned node is deleted: note becomes free-floating again
- TBD: exact visual treatment

## Flagging Nodes/Notes

- Option to flag/bookmark important nodes or notes for later reference
- Not designed yet

## System Instructions (rename from "Skill")

- Rename `skill` → `instructions` throughout backend and frontend
- Tree-level: prepended to every AI call in that tree
- When planting a new tree from a node, inherit the parent tree's instructions
- Fix injection: use agentbridge `system_prompt` parameter instead of prepending to user message
- UI: textarea in tree settings, labeled "Instructions (applied to every AI call in this tree)"
- Example: "Use Python 3.10 syntax. Write type hints. Add tests for every function."

---

## Implementation Order

### Phase 1: Core UX
1. **Remove chat panel** — delete ChatPanel.tsx and all references
2. **Chat vs edit node colors** — frontend check on git_commit
3. **Sidebar: all projects + trees** — backend: make server project-agnostic; frontend: redesign sidebar
4. **Remove node collapse** — delete expand/collapse feature + backend settings cleanup

### Phase 2: Polish
5. **Tool call auto-collapse** — show latest, collapse older
6. **Unread indicator** — glow on completion, dismiss on focus
7. **Rename skill → instructions** — backend rename + fix injection via system_prompt
8. **"New tree from node"** — right-click context menu on edit-nodes

### Phase 3: Later
9. Pin notes to nodes
10. Flag nodes/notes
11. GitHub clone flow in project creation
12. Desktop app (pywebview wrapper)
