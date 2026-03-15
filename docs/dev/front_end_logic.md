# Frontend UX Plan

## Sidebar: Projects & Trees

- Left panel shows **projects** (one per git repo) and **trees** under each project
- Projects and trees ranked by recency (most recently used at top)
- **Project creation**:
  - Click "new project" button in sidebar → assigns an empty folder by default
  - User can change the folder to a local git path or a GitHub URL
  - If the folder is not a git repo, warn and offer to `git init`
  - From CLI: `fission` in a git repo creates/finds the project and opens the most recent tree
  - Project name auto-detected from folder name or remote repo name, editable by user
- **Tree creation**:
  - Click "+" next to project name in sidebar
  - Or right-click an edit-node → "New tree from here" (inherits system instructions)
  - User is asked to name the tree (auto-summarized later from first message)
  - Defaults to current branch HEAD; user can change branch and commit

**Priority: high** — this is the main navigation. Backend already supports multi-project (trees have `repo_id`). Frontend sidebar needs redesign.

## Node Types: Chat vs Edit

- **Chat node**: AI responded but didn't change files → `git_commit == parent.git_commit`
- **Edit node**: AI modified files, a new commit was created → `git_commit != parent.git_commit`
- Edit nodes should have a different visual color/indicator than chat nodes
- No backend changes needed — the data is already there

**Priority: high** — small frontend change, big UX improvement for scanning a tree.

## Remove Chat Panel

- Delete the right-side chat panel entirely
- All interaction happens through the canvas (tree nodes)
- Long responses: click a node to expand in the existing NodeModal
- No backward compatibility needed

**Priority: high** — simplifies the UI, removes a redundant interaction surface.

## Tool Call Display

- Only show the latest 2-3 tool calls during streaming
- Auto-collapse older tool calls
- User can expand collapsed tools for inspection
- Don't remove tool calls from responses — just collapse them visually

**Priority: medium** — improves readability during long AI sessions.

## Unread Indicator

- When a chat completes, the node gets a visual indicator (glow, dot, color change)
- The indicator disappears when the user clicks/focuses the node
- Frontend-only state (no DB persistence) — decide later if backend tracking is needed

**Priority: medium** — helpful when multiple trees are active.

## Notes: Pin to Node

- Free-floating notes can optionally be pinned to a specific node
- Pinned notes stay visually near their node
- Implementation details TBD — need to decide behavior for collapsed/deleted nodes
- Adds `pinned_to_node_id` field to notes JSON

**Priority: low** — useful but edge-case. Get core UX right first.

## Remove Node Collapsing

- The expand/collapse subtree feature isn't useful in practice
- Remove it — users delete branches they don't want instead
- Simplifies the UI and removes `expanded_nodes` / `collapsed_subtrees` settings

**Priority: low** — cleanup, not urgent.

## Flagging Nodes/Notes

- Option to flag/bookmark important nodes or notes for later reference
- Not designed yet

**Priority: low** — nice to have, not essential.

## System Instructions (was "Skill")

- Rename `skill` → `instructions` throughout
- Tree-level instructions prepended to every AI call in that tree
- When planting a new tree from a node, inherit the parent tree's instructions
- Pass through agentbridge's `system_prompt` parameter (not prepended to user message)
- UI: textarea on tree settings, labeled "Instructions (applied to every AI call)"

**Priority: medium** — rename and fix injection method. Keep tree-level scope for now.

---

## Implementation Order

### Phase 1: Core UX (do now)
1. Remove chat panel
2. Chat vs edit node visual distinction
3. Sidebar redesign (projects + trees, ranked by recency)
4. Remove node collapsing feature

### Phase 2: Polish (do next)
5. Tool call auto-collapse
6. Unread indicator
7. Rename skill → instructions + fix injection
8. "New tree from node" context menu

### Phase 3: Nice to have (later)
9. Pin notes to nodes
10. Flag nodes/notes
11. GitHub clone flow in project creation
