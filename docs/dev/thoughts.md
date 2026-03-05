each node is either a plan node, or an edit node or executiond node. 
a plan node does not modify the repo. the user may click to see the plan doc.
an edit node will modify the repo. the user may click to see the diff and full repo.
an execution node will run some command to launch some job.

Need the feature to pause a node.
Need to let the agent evolve for the user. say after code is edited, it may be asked to automatically run it and show the results. then plan for the next steps.

work tree issues:
  - Lazy worktrees — only create the worktree when the user actually chats on that node,
  tear it down when idle (you already have ensure_worktree for the creation side)
  - Sparse checkout — only materialize files the AI actually touches
  - Copy-on-write filesystem (btrfs/APFS) — the OS deduplicates identical blocks
  transparently

parallel experiments -- duplicate nodes

the follow up window only shows when focused. It should show up even when the block is running -> creates a new child node that's dependent on the execution of the parent node and blocked by it waiting to be run. This way the user is scheduling jobs. there should also be a way to cancel a scheduled job or pause a running job.


what's the max agent tool calls currently?

can I send messages mid tool calls?

