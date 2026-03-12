# Git-Inspired Ideas for Coding Agent Trees

This document starts from one concrete optimization (ephemeral worktrees) and
expands into broader ideas about applying git's design principles to
tree-structured coding agents.

The underlying thesis: git solved the problem of managing divergent, parallel
lines of work on a codebase. Coding agents create the same problem — multiple
parallel explorations of the same codebase — but at a higher level. Git's
concepts (commits as truth, cheap branches, merge, cherry-pick, tags, diff)
translate almost directly.

---

## 1. Ephemeral worktrees

### Current behavior

Every node gets a persistent git worktree directory at
`~/.codefission/workspaces/<tree_id>/<node_id>/`. Worktrees are created when a
node is first used (`ensure_worktree`) and never removed until the entire tree
is deleted (`cleanup_tree_workspace`).

A linear conversation root -> A -> B -> C -> D produces 5 directories on disk,
each containing a full checkout of the project's files. The git object database
is shared (worktrees link back to the root's `.git`), but the working files are
duplicated in every directory.

For a 100 MB project with 20 nodes, that's ~2 GB of working files even though
only one node is typically active at a time.

### Key insight

After each agent response, `auto_commit` stages and commits all changes. The
full state of every node is already captured in git as a commit. The physical
working directory is only needed when:

1. An agent is actively streaming (reading/writing files).
2. The user is browsing files or viewing diffs.
3. A background process is still running in the workspace.

At all other times the worktree is dead weight — fully recoverable from the
commit.

### Design principle

**Commits are the source of truth; worktrees are disposable caches.**

This mirrors git itself. A git repository is the `.git` directory — the object
database, the refs, the DAG. The working tree is just a convenience for
humans. You can delete it and recreate it from any commit instantly.

### Suggested change

Make worktrees ephemeral: create on demand, remove when idle.

- **Creation**: `ensure_worktree` already handles this. No change needed.
- **Removal**: add a `remove_worktree` step after `auto_commit` completes and
  after file-browse responses are sent. Skip removal if an agent is streaming
  or a background process is alive in the workspace.
- **Restore**: `ensure_worktree` already checks existence and recreates from
  the stored commit. No change needed.

Disk usage drops from O(nodes x project_size) to O(active_agents x
project_size). For a 100 MB project with 20 nodes and 1 active agent, that's
~2 GB down to ~100 MB.

Backend-only change. The frontend never references worktree paths directly.

---

## 2. Merge — convergence after divergence

### The gap

The whole point of branching is to explore alternatives. But exploration is
only useful if you can converge on a result. Currently the tree only grows
outward — branches never rejoin.

### Code merge

Take two sibling nodes and `git merge` their branches into a new child node.
The user resolves conflicts if any. The result is a node whose workspace
contains the combined file changes from both approaches.

This is the literal git operation. It works because each node's branch is a
real git branch. The machinery already exists.

A merge node would have two parents in the conversation tree, mirroring a
merge commit's two parents in the git DAG. The conversation tree becomes a DAG
— which is fine, since the layout engine just needs to handle multiple parents.

### Conversation merge

A softer version. Create a new node whose prompt summarizes two branches:
"Approach A produced these files/results. Approach B produced these. Combine
the best parts." The agent reads both contexts and synthesizes.

This doesn't require git merge at all — it's a prompt-engineering pattern
built on the existing quoting system. The agent does the merging, not git.

### Why this matters

Without merge, branching is just parallel execution. With merge, branching
becomes a search strategy: explore broadly, then combine the best results.
This is the pattern that makes tree-of-thought reasoning powerful in LLM
research — and merge is what turns a tree of attempts into an actual outcome.

---

## 3. Cherry-pick — surgical transfer across branches

### The idea

You're working on branch B and you see that branch A made a great fix to one
file. You want that specific change, not everything else A did.

`git cherry-pick <commit>` applies a single commit's diff to the current
branch. In CodeFission, this means: select a node, pick specific commits (or the
node's single auto-commit), and apply them to another node's workspace.

### How it differs from quoting

The current quoting system works at the prompt level — it injects file
contents or diffs into the agent's context and asks it to use them. The agent
then rewrites the files from scratch.

Cherry-pick works at the code level — it applies the exact diff mechanically,
no agent involved. It's faster, deterministic, and doesn't consume tokens. The
agent can then be asked to build on top of the cherry-picked changes.

### Use case

Agent A writes a great utility function but goes off-track on the main task.
Agent B has the right architecture but is missing that utility. Cherry-pick
the utility from A into B. No need to re-explain anything to the agent.

---

## 4. Tags and bookmarks

### The problem

In a large tree, nodes blur together. Labels are auto-generated from the first
40 characters of the user message ("Implement the login page..."), which says
nothing about outcomes. Did it work? Was it the best approach? Is it a dead
end?

### Git's approach

Git tags are named pointers to commits. They're lightweight metadata — a name
and optionally a message, attached to an immutable snapshot.

### Suggested feature

Let users tag nodes with semantic labels: "working", "best", "dead end",
"promising", "shipped". Tags are visually distinct (color-coded badges on the
node) and filterable (show only "working" nodes across the tree).

This is cheap to implement — a tags field on the node model — but it
transforms navigation. Instead of expanding every branch to remember what
happened, you scan the tags.

For coding agents specifically, some tags could be auto-generated: "tests
pass", "build fails", "N files changed". The agent's exit status and the git
diff already provide this signal.

---

## 5. Diff any two nodes

### Current state

CodeFission shows diffs per node — each node's changes relative to its parent.
This answers "what did this turn change?" but not "how do these two approaches
differ?"

### Git's approach

`git diff <commit-a> <commit-b>` works for any two commits regardless of their
relationship in the DAG. Parent-child, siblings, cousins, completely unrelated
branches — all diffable.

### Suggested feature

Let users select any two nodes and see the diff between their committed states.
This is a single `git diff <commit-a> <commit-b>` call — trivial on the
backend.

This is the evaluation tool for tree-of-thought exploration. You asked two
agents to solve the same problem differently. How do the results compare? What
did approach A do that B didn't? The answer is in the diff.

---

## 6. Squash — compress verbose history

### The problem

Coding agents are verbose. A 10-node conversation chain where the first 8
turns are the agent fumbling (wrong file path, missing import, test failure,
retry) and the last 2 are the solution. The tree is cluttered with noise.

### Git's approach

`git rebase --squash` compresses a chain of commits into one. The intermediate
steps disappear; only the final result remains.

### Suggested feature

Let users select a chain of nodes and squash them into a single node. The
squashed node keeps the final commit (the code outcome) and a summary of the
conversation (auto-generated or user-written). The intermediate nodes are
hidden or deleted.

This is particularly valuable for coding agents because the signal-to-noise
ratio of agent conversations is low. The user cares about what was built, not
the 6 failed attempts. Squash preserves the result while cleaning the tree.

A softer version: collapse a chain visually (the subtree-collapse feature
already exists) but add the ability to label the collapsed group. "Login page
implementation (8 turns, squashed)."

---

## 7. Bisect — find where things went wrong

### Git's approach

`git bisect` binary-searches through commit history to find which commit
introduced a bug. It checks out commits, you test, it narrows the range.

### For coding agents

In a long conversation chain, at what point did the agent go off-track? Bisect
would let you jump to the midpoint, check the workspace state, and tell the
system "good" or "bad." It narrows down which turn introduced the problem.

This is less about automation and more about navigation. In a 20-node chain,
manually expanding each node to find where things diverged is painful. Bisect
gives you a structured way to do it in ~4-5 steps.

Could also be automated if nodes have test suites: run the tests at each
node's commit and find the first failure. Same as `git bisect run`.

---

## 8. Rebase — replay on a new base

### The idea

You branched from node A and did 5 turns of good work. Meanwhile, someone
(another agent, or you manually) improved node A. Your 5 turns are based on
the old version.

Git rebase replays your commits on top of the updated base. For CodeFission:
take a chain of nodes and re-apply their prompts starting from a different
parent node, producing new agent responses against the updated codebase.

### Why this is different from just re-running

A naive approach is to re-send the same messages from a new starting point.
But that loses the agent's session context and may produce completely different
results.

A smarter rebase would fork the session from the new parent and feed back the
original user messages one at a time, letting the agent adapt. Conflicts (the
agent's changes clash with the new base) surface naturally as the agent tries
to apply changes that no longer make sense.

This is expensive (re-runs all the agent turns) but enables a powerful
workflow: refine the foundation, then automatically propagate improvements to
all downstream branches.

---

## Summary

| Git concept      | CodeFission analog                  | Status       |
|------------------|----------------------------------|--------------|
| Commit as truth  | Ephemeral worktrees              | Proposed     |
| Branch           | Conversation node                | Implemented  |
| Merge            | Combine two exploration branches | Missing      |
| Cherry-pick      | Apply specific changes across branches | Missing |
| Tag              | Bookmark nodes with outcomes     | Missing      |
| Diff (arbitrary) | Compare any two nodes            | Missing      |
| Squash           | Compress verbose agent chains    | Missing      |
| Bisect           | Find where agent went off-track  | Missing      |
| Rebase           | Replay prompts on updated base   | Missing      |

The theme across all of these: git treats code history as a first-class data
structure with rich operations. CodeFission has the data structure (the
conversation tree backed by real git commits) but only the most basic
operation (branch). Adding the rest of git's vocabulary — merge, cherry-pick,
diff, squash, tag — turns a branching chat tool into a proper exploration and
convergence engine for coding agents.
