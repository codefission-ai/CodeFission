# Mount Namespace Workspace Isolation (Future)

## Problem

When a child node forks from a parent's Claude SDK session, the conversation
history contains file paths from the parent's worktree (e.g.
`/workspaces/<tree>/<parent-node>/src/main.py`). The model sees these paths and
reuses them, writing files to the parent's directory instead of its own worktree.

## Current Mitigation

We inject a `[System: ...]` notice into the user message at fork time, telling
the model its working directory has changed. This works but relies on the model
obeying the instruction.

## Advanced Solution: Linux Mount Namespaces

Use `unshare(CLONE_NEWNS)` + `mount --bind` so that every SDK subprocess sees
**the same path** (e.g. `/workspace`) regardless of which worktree it actually
maps to. This eliminates the problem entirely because the model never sees a
path change.

### How it works

1. Before spawning the Claude SDK process for a node, create a new mount
   namespace via `unshare -m`.
2. Bind-mount the node's worktree onto a fixed canonical path:
   ```bash
   mount --bind /workspaces/<tree>/<node-id> /workspace
   ```
3. Set `cwd=/workspace` in `ClaudeAgentOptions`.
4. The SDK process (and all its child tools — bash, file reads, etc.) see
   `/workspace` as their working directory. The conversation history from the
   parent also referenced `/workspace`, so paths are consistent.

### Benefits

- **No path confusion** — parent and child both use `/workspace`.
- **Prompt cache preserved** — session forking still works because the paths in
  the cached conversation match the child's view.
- **Parallel execution safe** — each process has its own mount namespace, so
  multiple nodes can run simultaneously without conflicts.
- **No model cooperation needed** — this is OS-level isolation, not a prompt
  hack.

### Requirements

- Linux only (mount namespaces are a Linux kernel feature).
- Needs `CAP_SYS_ADMIN` or `unshare` with user namespace support.
- Python: use `subprocess.Popen` with `preexec_fn` that calls
  `ctypes.CDLL('libc.so.6').unshare(CLONE_NEWNS)`, or shell out to
  `unshare -m --propagation private`.

### Implementation sketch

```python
import ctypes
import subprocess

CLONE_NEWNS = 0x00020000
libc = ctypes.CDLL("libc.so.6", use_errno=True)

def _setup_mount_ns(worktree_path: str, canonical: str = "/workspace"):
    """preexec_fn for subprocess: new mount ns + bind mount."""
    if libc.unshare(CLONE_NEWNS) != 0:
        raise OSError("unshare(CLONE_NEWNS) failed")
    # Make mounts private so they don't leak
    subprocess.check_call(["mount", "--make-rprivate", "/"])
    subprocess.check_call(["mount", "--bind", worktree_path, canonical])
    import os
    os.chdir(canonical)
```

### Caveats

- Requires root or `CAP_SYS_ADMIN` (user namespaces can help but add
  complexity).
- macOS has no mount namespaces; would need a different approach there.
- Testing is harder — need to verify namespace isolation in CI.

### When to implement

Consider this when:
- The prompt-injection approach proves unreliable in practice.
- We need to support long multi-turn conversations where the model "forgets"
  the workspace notice.
- We want to run untrusted code in stronger isolation anyway.
