"""Workspace service — git worktree management for per-node isolation.

Works directly with the user's project repo.
Root nodes resolve to the project path; child nodes get git worktrees
in {project}/.codefission/worktrees/{node_id}.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from config import get_project_path, get_project_dir

log = logging.getLogger(__name__)

# Git environment for CodeFission commits (avoids modifying user's repo config)
_GIT_ENV = {
    "GIT_COMMITTER_NAME": "CodeFission",
    "GIT_COMMITTER_EMAIL": "codefission@local",
    "GIT_AUTHOR_NAME": "CodeFission",
    "GIT_AUTHOR_EMAIL": "codefission@local",
}


def _worktrees_dir() -> Path:
    return get_project_dir() / "worktrees"


def _artifacts_dir() -> Path:
    return get_project_dir() / "artifacts"


# ── Low-level git helper ─────────────────────────────────────────────

async def _run_git(cwd: Path, *args: str, check: bool = True, env: dict | None = None) -> tuple[int, str, str]:
    """Run a git command asynchronously, return (returncode, stdout, stderr)."""
    full_env = {**os.environ, **(env or {})}
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=full_env,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode(errors="replace").strip()
    stderr = stderr_bytes.decode(errors="replace").strip()
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (rc={proc.returncode}): {stderr}")
    return proc.returncode, stdout, stderr


async def _run_git_raw(cwd: Path, *args: str, check: bool = True) -> tuple[int, bytes, str]:
    """Run a git command, return raw stdout bytes (for binary content)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stderr = stderr_bytes.decode(errors="replace").strip()
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (rc={proc.returncode}): {stderr}")
    return proc.returncode, stdout_bytes, stderr


# ── Workspace resolution ─────────────────────────────────────────────

def resolve_workspace(root_id: str, node_id: str) -> Path:
    """Return the workspace path for a node.

    Root node → project path (user's actual repo).
    Child nodes → .codefission/worktrees/{node_id}.
    """
    if node_id == root_id:
        return get_project_path()
    return _worktrees_dir() / node_id


# ── Worktree management ──────────────────────────────────────────────

async def create_worktree(node_id: str, from_commit: str) -> Path:
    """Create a git worktree for a node, branching from the given commit."""
    project_path = get_project_path()
    wt_dir = _worktrees_dir()
    worktree_path = wt_dir / node_id
    branch_name = f"ct-{node_id}"
    wt_dir.mkdir(parents=True, exist_ok=True)
    await _run_git(
        project_path,
        "worktree", "add",
        str(worktree_path),
        "-b", branch_name,
        from_commit,
    )
    return worktree_path


async def ensure_worktree(
    root_id: str, node_id: str,
    parent_id: str | None, parent_commit: str | None,
) -> Path:
    """Ensure a worktree exists for the node, creating it if needed."""
    project_path = get_project_path()
    wt_dir = _worktrees_dir()

    # Root node — use project path directly
    if node_id == root_id:
        return project_path

    worktree_path = wt_dir / node_id

    # Already exists
    if worktree_path.exists():
        return worktree_path

    # Check if branch already exists (e.g. worktree was removed but branch remains)
    branch_name = f"ct-{node_id}"
    rc, _, _ = await _run_git(project_path, "rev-parse", "--verify", branch_name, check=False)
    if rc == 0:
        # Branch exists — attach worktree to existing branch (no -b)
        wt_dir.mkdir(parents=True, exist_ok=True)
        await _run_git(project_path, "worktree", "add", str(worktree_path), branch_name)
        return worktree_path

    # Determine parent's commit
    if not parent_commit:
        # Try reading HEAD from parent's worktree first
        parent_ws = resolve_workspace(root_id, parent_id or root_id)
        if parent_ws.exists():
            _, parent_commit, _ = await _run_git(parent_ws, "rev-parse", "HEAD")
        else:
            # Parent worktree removed — resolve from branch ref in main repo
            parent_branch = f"ct-{parent_id}" if parent_id and parent_id != root_id else "HEAD"
            _, parent_commit, _ = await _run_git(project_path, "rev-parse", parent_branch)

    return await create_worktree(node_id, parent_commit)


async def remove_worktree_and_branch(root_id: str, node_id: str) -> bool:
    """Remove a node's worktree AND its branch ref.

    Skips root nodes. Returns True if removal happened.
    """
    if node_id == root_id:
        return False

    removed = await remove_worktree(root_id, node_id)

    # Delete the branch ref from the main repo
    project_path = get_project_path()
    branch_name = f"ct-{node_id}"
    try:
        await _run_git(project_path, "branch", "-D", branch_name, check=False)
    except Exception as e:
        log.debug("Branch deletion failed for %s: %s", branch_name, e)

    return removed


async def remove_worktree(root_id: str, node_id: str) -> bool:
    """Remove a node's worktree directory, keeping the branch ref.

    Skips root nodes. Returns True if removal happened.
    """
    if node_id == root_id:
        return False

    project_path = get_project_path()
    worktree_path = _worktrees_dir() / node_id
    if not worktree_path.exists():
        return False

    try:
        await _run_git(project_path, "worktree", "remove", "--force", str(worktree_path))
    except Exception:
        # Fallback: manual removal + prune
        try:
            shutil.rmtree(worktree_path, ignore_errors=True)
            await _run_git(project_path, "worktree", "prune", check=False)
        except Exception as e2:
            log.warning("Worktree removal fallback failed: %s", e2)
            return False

    log.info("Removed worktree for node %s", node_id)
    return True


# ── Git-based reading (no worktree needed) ───────────────────────────

_HIDDEN_FILES = {".gitignore"}


async def list_files_from_commit(commit: str) -> list[str]:
    """List files at a commit using git ls-tree (no worktree needed)."""
    project_path = get_project_path()
    _, out, _ = await _run_git(project_path, "ls-tree", "-r", "--name-only", commit)
    return [f for f in out.splitlines() if f and f not in _HIDDEN_FILES] if out else []


async def read_file_from_commit(commit: str, file_path: str) -> str:
    """Read a text file at a commit using git show (no worktree needed)."""
    project_path = get_project_path()
    _, content, _ = await _run_git(project_path, "show", f"{commit}:{file_path}")
    return content


async def read_file_bytes_from_commit(commit: str, file_path: str) -> bytes:
    """Read a file at a commit as raw bytes (safe for binary files)."""
    project_path = get_project_path()
    _, raw, _ = await _run_git_raw(project_path, "show", f"{commit}:{file_path}")
    return raw


async def get_diff_from_commits(parent_commit: str | None, commit: str) -> str:
    """Get diff between two commits (no worktree needed)."""
    project_path = get_project_path()
    if parent_commit:
        _, diff, _ = await _run_git(project_path, "diff", parent_commit, commit, check=False)
    else:
        _, diff, _ = await _run_git(
            project_path, "diff", "4b825dc642cb6eb9a060e54bf899d15006578e83", commit, check=False
        )
    return diff


# ── Auto-commit ───────────────────────────────────────────────────────

async def auto_commit(worktree_path: Path, message: str) -> tuple[str, int]:
    """Stage all changes and commit. Returns (HEAD sha, files_changed).

    Uses environment variables for committer identity (never modifies repo config).
    Skips auto-commit on the project path (the user's working tree is read-only).
    """
    # Never auto-commit to the user's actual working tree
    project_path = get_project_path()
    if worktree_path.resolve() == project_path.resolve():
        _, sha, _ = await _run_git(worktree_path, "rev-parse", "HEAD")
        return sha, 0

    # Ensure _artifacts/ is gitignored (migration for existing repos)
    gitignore = worktree_path / ".gitignore"
    if gitignore.exists():
        existing = gitignore.read_text()
        if "_artifacts/" not in existing:
            with open(gitignore, "a") as f:
                f.write("\n_artifacts/\n")

    await _run_git(worktree_path, "add", "-A")

    # Check if there are staged changes
    rc, _, _ = await _run_git(worktree_path, "diff", "--cached", "--quiet", check=False)
    files_changed = 0

    if rc != 0:
        # Count changed files before committing
        _, diff_names, _ = await _run_git(worktree_path, "diff", "--cached", "--name-only")
        files_changed = len(diff_names.splitlines()) if diff_names else 0

        # Commit with CodeFission identity via env vars
        commit_msg = f"ct: {message[:72]}"
        await _run_git(worktree_path, "commit", "-m", commit_msg, env=_GIT_ENV)

    # Return current HEAD regardless
    _, sha, _ = await _run_git(worktree_path, "rev-parse", "HEAD")
    return sha, files_changed


# ── Session portability ───────────────────────────────────────────

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _claude_project_dir(workspace: Path) -> Path:
    """Return the Claude Code project dir for a workspace path.

    Claude Code encodes the cwd by replacing both / and . with dashes.
    E.g. /home/user/.foo/bar → -home-user--foo-bar
    """
    encoded = str(workspace.resolve()).replace("/", "-").replace(".", "-")
    return CLAUDE_PROJECTS_DIR / encoded


def session_file_exists(workspace: Path, session_id: str) -> bool:
    """Check if a session file exists for the given workspace and session_id."""
    return (_claude_project_dir(workspace) / f"{session_id}.jsonl").exists()


def copy_session(parent_workspace: Path, child_workspace: Path, session_id: str):
    """Copy a session file from the parent's project dir to the child's.

    This allows the SDK to fork a session that was created with a
    different cwd (i.e. a different worktree).
    """
    src_dir = _claude_project_dir(parent_workspace)
    dst_dir = _claude_project_dir(child_workspace)
    src_file = src_dir / f"{session_id}.jsonl"
    if not src_file.exists():
        log.warning("Parent session file not found: %s", src_file)
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst_file = dst_dir / f"{session_id}.jsonl"
    if not dst_file.exists():
        shutil.copy2(str(src_file), str(dst_file))


# ── File browsing / diff ──────────────────────────────────────────

async def list_files(worktree_path: Path) -> list[str]:
    """List tracked + untracked files (excluding .git internals)."""
    _, out, _ = await _run_git(worktree_path, "ls-files", "-co", "--exclude-standard")
    return [f for f in out.splitlines() if f and f not in _HIDDEN_FILES] if out else []


async def get_diff(worktree_path: Path, parent_commit: str | None) -> str:
    """Get unified diff of changes. If parent_commit is None, diff against empty tree."""
    if parent_commit:
        _, diff, _ = await _run_git(worktree_path, "diff", parent_commit, "HEAD", check=False)
    else:
        # Diff entire tree against empty tree (shows all files as added)
        _, diff, _ = await _run_git(
            worktree_path, "diff", "4b825dc642cb6eb9a060e54bf899d15006578e83", "HEAD", check=False
        )
    return diff


def read_file(worktree_path: Path, file_path: str) -> str:
    """Read a file from a worktree with path traversal protection and size limit."""
    resolved = (worktree_path / file_path).resolve()
    if not str(resolved).startswith(str(worktree_path.resolve())):
        raise ValueError("Path traversal detected")
    if not resolved.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")
    size = resolved.stat().st_size
    if size > 1_048_576:  # 1MB
        raise ValueError(f"File too large: {size} bytes")
    return resolved.read_text(errors="replace")


# ── Artifact persistence ──────────────────────────────────────────────

def persist_artifacts(worktree_path: Path, node_id: str) -> int:
    """Copy _artifacts/ from worktree to artifacts dir.

    Returns the number of files copied.
    """
    src = worktree_path / "_artifacts"
    if not src.is_dir():
        return 0
    dst = _artifacts_dir() / node_id
    count = 0
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(f), str(target))
        count += 1
    if count:
        log.info("Persisted %d artifact(s) for node %s", count, node_id)
    return count


def read_artifact_bytes(node_id: str, file_path: str) -> bytes | None:
    """Read a persisted artifact file. Returns None if not found.

    file_path may include or omit the _artifacts/ prefix.
    Includes path traversal protection.
    """
    # Strip _artifacts/ prefix if present
    clean = file_path
    if clean.startswith("_artifacts/"):
        clean = clean[len("_artifacts/"):]

    artifacts_dir = _artifacts_dir()
    target = (artifacts_dir / node_id / clean).resolve()
    expected_prefix = str((artifacts_dir / node_id).resolve())
    if not str(target).startswith(expected_prefix):
        return None  # path traversal
    if not target.is_file():
        return None
    return target.read_bytes()


def list_artifact_files(node_id: str) -> list[str]:
    """List persisted artifact files, prefixed with _artifacts/."""
    base = _artifacts_dir() / node_id
    if not base.is_dir():
        return []
    return [
        f"_artifacts/{f.relative_to(base)}"
        for f in sorted(base.rglob("*"))
        if f.is_file()
    ]


# ── Branch operations ─────────────────────────────────────────────────

async def list_branches() -> list[dict]:
    """Return local branches from project path with current branch marked."""
    project_path = get_project_path()
    _, current, _ = await _run_git(project_path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    _, out, _ = await _run_git(project_path, "branch", "--format=%(refname:short)", check=False)
    branches = []
    for name in out.splitlines():
        name = name.strip()
        if not name or name.startswith("ct-"):
            continue  # Skip CodeFission internal branches
        branches.append({"name": name, "current": name == current})
    return branches


async def merge_to_branch(source_branch: str, target_branch: str) -> dict:
    """Squash merge source_branch into target_branch.

    Returns {"ok": True, "commit": sha} on success,
    or {"ok": False, "conflicts": [...]} on conflict.
    """
    project_path = get_project_path()

    # Ensure we're on the target branch
    _, current, _ = await _run_git(project_path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    if current != target_branch:
        await _run_git(project_path, "checkout", target_branch)

    # Check for uncommitted changes
    rc, _, _ = await _run_git(project_path, "diff", "--quiet", check=False)
    rc2, _, _ = await _run_git(project_path, "diff", "--cached", "--quiet", check=False)
    if rc != 0 or rc2 != 0:
        return {"ok": False, "error": "Target branch has uncommitted changes. Commit or stash them first."}

    # Squash merge
    rc, _, stderr = await _run_git(project_path, "merge", "--squash", source_branch, check=False)
    if rc != 0:
        # Check for conflicts
        _, status, _ = await _run_git(project_path, "diff", "--name-only", "--diff-filter=U", check=False)
        conflicts = [f for f in status.splitlines() if f]
        if conflicts:
            # Abort the merge
            await _run_git(project_path, "merge", "--abort", check=False)
            # Reset any staged changes
            await _run_git(project_path, "reset", "HEAD", check=False)
            await _run_git(project_path, "checkout", ".", check=False)
            return {"ok": False, "conflicts": conflicts}
        return {"ok": False, "error": f"Merge failed: {stderr}"}

    # Commit the squash merge
    commit_msg = f"Merge {source_branch} (squash)"
    await _run_git(project_path, "commit", "-m", commit_msg, env=_GIT_ENV, check=False)
    _, sha, _ = await _run_git(project_path, "rev-parse", "HEAD")
    return {"ok": True, "commit": sha}


# ── Repo identity ─────────────────────────────────────────────────────

async def compute_repo_id(repo_path: Path) -> str:
    """Compute a stable repo identity: SHA of the initial commit."""
    _, out, _ = await _run_git(repo_path, "rev-list", "--max-parents=0", "HEAD")
    # May return multiple roots (rare); use first
    return out.splitlines()[0].strip()


def detect_repo_name(repo_path: Path) -> str:
    """Auto-detect repo name: GitHub remote path or directory basename."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(repo_path),
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            for prefix in ("https://github.com/", "https://gitlab.com/",
                           "git@github.com:", "git@gitlab.com:"):
                if url.startswith(prefix):
                    path = url[len(prefix):]
                    if path.endswith(".git"):
                        path = path[:-4]
                    return path
    except Exception:
        pass
    return repo_path.name


# ── Project/repo info ─────────────────────────────────────────────────

async def get_repo_info(repo_path: Path | None = None) -> dict:
    """Return repo path, name, current branch, and dirty status."""
    if repo_path is None:
        repo_path = get_project_path()
    _, branch, _ = await _run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    _, status_output, _ = await _run_git(repo_path, "status", "--porcelain", check=False)
    is_dirty = bool(status_output.strip())

    return {
        "path": str(repo_path),
        "name": repo_path.name,
        "current_branch": branch,
        "is_dirty": is_dirty,
    }


# Keep old name as alias for backward compatibility within this module
async def get_project_info() -> dict:
    return await get_repo_info()


# ── Staleness detection ──────────────────────────────────────────────

async def check_staleness(base_branch: str, base_commit: str | None) -> dict:
    """Compare base_branch HEAD with stored base_commit.

    Returns {"stale": False} or {"stale": True, "commits_behind": N, "branch_head": sha}.
    """
    if not base_commit:
        return {"stale": False, "commits_behind": 0}

    project_path = get_project_path()
    rc, head_sha, _ = await _run_git(project_path, "rev-parse", base_branch, check=False)
    if rc != 0:
        return {"stale": False, "commits_behind": 0, "branch_missing": True}

    if head_sha == base_commit:
        return {"stale": False, "commits_behind": 0}

    _, count_str, _ = await _run_git(
        project_path, "rev-list", "--count", f"{base_commit}..{head_sha}", check=False
    )
    commits_behind = int(count_str) if count_str.strip().isdigit() else 0
    return {"stale": True, "commits_behind": commits_behind, "branch_head": head_sha}


# ── Protective git ref ───────────────────────────────────────────────

async def create_protective_ref(tree_id: str, commit: str):
    """Create a git ref to prevent GC of the base commit."""
    project_path = get_project_path()
    await _run_git(project_path, "update-ref", f"refs/codefission/{tree_id}", commit, check=False)


async def delete_protective_ref(tree_id: str):
    """Remove the protective git ref."""
    project_path = get_project_path()
    await _run_git(project_path, "update-ref", "-d", f"refs/codefission/{tree_id}", check=False)


# ── Cleanup ───────────────────────────────────────────────────────────

def cleanup_tree_workspaces(project_path: Path, root_id: str, node_ids: list[str]):
    """Remove worktrees and branches for a tree's nodes.

    Takes project_path explicitly since this may be called from sync context.
    Never touches the project path itself.
    """
    from store.processes import kill_all_in_workspace

    worktrees_dir = project_path / ".codefission" / "worktrees"
    artifacts_dir = project_path / ".codefission" / "artifacts"

    for nid in node_ids:
        if nid == root_id:
            continue
        worktree_path = worktrees_dir / nid
        if worktree_path.exists():
            kill_all_in_workspace(worktree_path)
            shutil.rmtree(worktree_path, ignore_errors=True)

    # Prune dangling worktree references
    import subprocess
    try:
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=str(project_path),
            capture_output=True,
        )
    except Exception:
        pass

    # Clean up ct-* branches
    for nid in node_ids:
        if nid == root_id:
            continue
        branch_name = f"ct-{nid}"
        try:
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=str(project_path),
                capture_output=True,
            )
        except Exception:
            pass

    # Clean up persisted artifacts
    for nid in node_ids:
        artifact_dir = artifacts_dir / nid
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir, ignore_errors=True)
