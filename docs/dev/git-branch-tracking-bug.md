# git_branch Tracking Bug + Merge UX Design

_Written: 2026-03-19_

---

## The Bug

### Root Cause

`git_branch` is **never written to the database for regular chat nodes**.

When a chat completes, the node ends up with:

| field | set? | value |
|---|---|---|
| `git_commit` | ✅ | `C_A` — the auto-commit SHA |
| `session_id` | ✅ | `S_A` — the Claude session ID |
| `git_branch` | ❌ | `NULL` — never stored |

Yet the git branch `ct-{node_id}` **does exist** in the repo (created by `ensure_worktree` at chat start, and kept by `post_chat_cleanup` when `files_changed > 0`).

### Where it's set vs. where it should be

| Node type | `git_branch` in DB | Actual git branch |
|---|---|---|
| Root | `"main"` (set in `create_tree`) | main ✅ |
| Chat node | `NULL` | `ct-{node_id}` exists ✅ |
| Branch passthrough | `"ct-{node.id}"` (set in `branch()`) | **does not exist** ❌ |
| Draft node | `"ct-{node.id}"` (set in `prepare_draft`) | exists ✅ |

The branch passthrough node (`branch()`) is the inverse problem: it records a branch name in the DB that is never created in git. That path is also dead in the current frontend (`WS.BRANCH` is defined but never sent).

### User-visible symptom

The frontend shows **"Merge to main"** when `node.git_commit !== parentCommit` (i.e. the AI changed files). The button appears correctly. But clicking it always fails:

```
merge_to_branch → node.git_branch is NULL → "Node has no branch"
```

So the merge feature is **always broken** for the nodes it's meant to work on.

### Proposed Fix

In `_finish_prepare_chat` (or immediately after `ensure_worktree` succeeds), store the branch name:

```python
branch_name = f"ct-{nid}"
await ensure_worktree(...)
await update_node(nid, git_branch=branch_name)
```

Additionally, in `merge_to_branch`, add a fallback for nodes that slip through with a NULL git_branch (backward-compat for existing data):

```python
effective_branch = node.git_branch or f"ct-{node_id}"
```

And clean up the dead `branch()` / `handle_branch` / `WS.BRANCH` path — or decide if it's the right abstraction and wire it back up.

---

## UX Design Question: What Should "Merge" Actually Mean?

This is the harder problem. The bug fix is mechanical, but the merge UX needs deliberate design. Here's the design space.

### What a user actually wants

Users will typically be running **a few parallel AI-assisted feature branches**. Each "tree" in CodeFission maps naturally to one feature or investigation. Within a tree, they'll have a chain of turns (A → A2 → A3) where each node builds on the previous one.

When they're happy with the result, they want to **ship it** — get those changes into their main branch.

At that point the questions are:

1. **Which node's changes?** The latest in the chain (A3)? All accumulated changes from the whole thread?
2. **Which target?** Main branch? Another feature branch? Another tree's latest node?
3. **What about conflicts?** If two trees both modified the same file from different starting points?

### Three distinct workflows to support

#### 1. Merge a thread to main (the common case)

The user finishes a series of AI turns and wants to ship. They click "Merge to main" on the last node in the chain.

**What should this squash-merge?** The current implementation squash-merges `ct-{node_id}`, which is only the single-turn branch. But the user's work spans A → A2 → A3, each on its own branch.

Two sub-options:
- **Cumulative squash**: Squash everything from tree's base commit to `node.git_commit`. Produces one clean commit in main with all AI work combined. Simple, common in AI-assisted workflows.
- **Per-turn preserve**: Keep the individual commits. More git history but noisy.

**Recommendation**: Cumulative squash is the right default for AI-assisted work. The individual commits (`ct-{node.id}`) are exploration artifacts, not deliverables. One PR-style squash commit in main is what the user wants.

#### 2. Merge between trees / branches (cross-pollination)

The user has Feature A tree and Feature B tree. They want a third environment that has both A and B together — to test integration, or because they're related.

This is harder than it sounds: A and B may have diverged from the same base commit, but both have changed code. Git merge would work, but there might be conflicts.

**Options**:
- **New tree from merged commit**: Create a new tree rooted at a manual merge of A and B. The user does the conflict resolution outside the app.
- **"Rebase onto" action**: Take Feature B's changes and replay them on top of Feature A's latest commit. Similar to `git rebase`.
- **Quote-and-redo** (already exists!): The user quotes files from branch A into a message in branch B and asks the AI to integrate them. This is the most AI-native approach — no git plumbing needed.

**Recommendation**: The "quote" feature is already the right tool for cross-pollination. No new merge UX needed for this case. Document it as the intended workflow.

#### 3. Independent feature merges to main

User has Tree A (Feature A, done) and Tree B (Feature B, still in progress). They want to merge A to main without touching B.

This works fine today if the merge is a squash — each tree's work is independent. The only issue is that after A merges, Tree B's base commit is stale (main has moved). The staleness indicator already detects this.

**The missing piece**: after A merges, the user needs a way to "rebase" Tree B onto the new main. Options:
- **"Update base"** (already exists): The user can update the tree's base commit. But they'd have to resolve any conflicts manually and the old conversation context no longer matches the new code.
- **New tree from updated commit**: Plant a new tree at the merged main HEAD, quote relevant conversation from Tree B, and continue from there.

**Recommendation**: Rebasing a tree after another merges is a hard UX problem. For now, document the "plant new tree + quote context" pattern as the workaround. A future "Rebase tree" feature could automate this.

### Suggested merge UX changes

1. **Fix the bug** (set `git_branch` for chat nodes — see above).

2. **Change squash-merge to be cumulative**: Instead of merging only `ct-{node_id}`, compute the diff between `tree.base_commit` and `node.git_commit` and apply that as a single squash commit. This is a `git diff {base}..{node_commit} | git apply` or a `git merge --squash` from the node's commit treated as a diverged branch.

3. **Show a pre-merge diff preview**: Before the user confirms merge, show them exactly what will land in main. The diff panel already exists; just wire it into the merge flow with a confirm step.

4. **Merge target should not be hardcoded to `base_branch`**: Let the user choose — main, another branch, or another tree's latest node. Useful for the cross-tree workflow.

5. **Post-merge staleness on sibling trees**: After a successful merge, automatically flag all sibling trees for the same repo as stale (they already detect it, just make it more prominent).

### What the branch passthrough (`WS.BRANCH`) was meant to be

The passthrough node exists to let users "fork" without committing to a new message — a bookmark/branch-point. It's a reasonable concept but the frontend never exposed it. Two paths forward:

- **Remove it entirely**: Natural branching already happens when multiple messages are sent from the same node. The passthrough adds nothing.
- **Expose it as a UI concept**: A "bookmark this point" button that creates a visible split in the tree, with a label. Useful for explicitly marking "I tried two approaches from here". Requires fixing the git_branch creation (the branch needs to actually exist in git when the passthrough is created).

The current code leaves `ct-{B.id}` uncreated, making `branch()` half-implemented in both directions. Either finish it or remove it.
