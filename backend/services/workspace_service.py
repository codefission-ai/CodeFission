"""Workspace service — git worktree management for per-node isolation.

Every tree gets a git repo at the root node's workspace directory.
Child nodes get git worktrees branched from their parent's commit.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from config import DATA_DIR

log = logging.getLogger(__name__)

WORKSPACES_DIR = DATA_DIR / "workspaces"


# ── Low-level git helper ─────────────────────────────────────────────

async def _run_git(cwd: Path, *args: str, check: bool = True) -> tuple[int, str, str]:
    """Run a git command asynchronously, return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode(errors="replace").strip()
    stderr = stderr_bytes.decode(errors="replace").strip()
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (rc={proc.returncode}): {stderr}")
    return proc.returncode, stdout, stderr


# ── Repo setup ────────────────────────────────────────────────────────

async def setup_repo(tree_id: str, root_id: str, repo_mode: str, repo_source: str | None) -> Path:
    """Initialise the main git repo for a tree. Returns the root workspace path.

    Idempotent — returns immediately if a git repo already exists in root_dir.

    Modes:
      "new"   — git init + initial commit
      "local" — git clone from local path
      "url"   — git clone from URL
    """
    root_dir = WORKSPACES_DIR / tree_id / root_id
    root_dir.mkdir(parents=True, exist_ok=True)

    # Idempotent: if .git already exists, skip initialisation
    if (root_dir / ".git").exists():
        return root_dir

    if repo_mode == "new":
        await _run_git(root_dir, "init")
        gitignore = root_dir / ".gitignore"
        gitignore.write_text(".claude/\n")
        await _run_git(root_dir, "add", "-A")
        await _run_git(root_dir, "commit", "-m", "ct: initial commit")

    elif repo_mode in ("local", "url"):
        if not repo_source:
            raise ValueError(f"repo_source required for mode '{repo_mode}'")
        # Clone to a temp dir then move contents into root_dir
        with tempfile.TemporaryDirectory() as tmp:
            tmp_clone = Path(tmp) / "clone"
            await _run_git(Path(tmp), "clone", repo_source, str(tmp_clone))
            # Move everything (including .git) into root_dir
            for item in tmp_clone.iterdir():
                dest = root_dir / item.name
                shutil.move(str(item), str(dest))
        # Ensure .claude/ is gitignored
        gitignore = root_dir / ".gitignore"
        existing = gitignore.read_text() if gitignore.exists() else ""
        if ".claude/" not in existing:
            with open(gitignore, "a") as f:
                f.write("\n.claude/\n")
            await _run_git(root_dir, "add", ".gitignore")
            rc, _, _ = await _run_git(root_dir, "diff", "--cached", "--quiet", check=False)
            if rc != 0:
                await _run_git(root_dir, "commit", "-m", "ct: add .claude/ to .gitignore")

    else:
        raise ValueError(f"Unknown repo_mode: {repo_mode}")

    # Configure committer identity (inherited by worktrees)
    await _run_git(root_dir, "config", "user.email", "repoevolve@local")
    await _run_git(root_dir, "config", "user.name", "RepoEvolve")

    return root_dir


# ── Worktree management ──────────────────────────────────────────────

async def create_worktree(tree_id: str, root_id: str, node_id: str, from_commit: str) -> Path:
    """Create a git worktree for a node, branching from the given commit."""
    main_repo = WORKSPACES_DIR / tree_id / root_id
    worktree_path = WORKSPACES_DIR / tree_id / node_id
    branch_name = f"ct-{node_id}"
    await _run_git(
        main_repo,
        "worktree", "add",
        str(worktree_path),
        "-b", branch_name,
        from_commit,
    )
    return worktree_path


async def ensure_worktree(
    tree_id: str, root_id: str, node_id: str,
    parent_id: str | None, parent_commit: str | None,
) -> Path:
    """Ensure a worktree exists for the node, creating it if needed."""
    worktree_path = WORKSPACES_DIR / tree_id / node_id

    # Already exists
    if worktree_path.exists():
        return worktree_path

    # Root node — should already exist from setup_repo
    if node_id == root_id:
        if not worktree_path.exists():
            raise RuntimeError(f"Root workspace missing: {worktree_path}")
        return worktree_path

    # Check if branch already exists (e.g. worktree was removed but branch remains)
    main_repo = WORKSPACES_DIR / tree_id / root_id
    branch_name = f"ct-{node_id}"
    rc, _, _ = await _run_git(main_repo, "rev-parse", "--verify", branch_name, check=False)
    if rc == 0:
        # Branch exists — attach worktree to existing branch (no -b)
        await _run_git(main_repo, "worktree", "add", str(worktree_path), branch_name)
        return worktree_path

    # Determine parent's commit
    if not parent_commit:
        # Try reading HEAD from parent's worktree first
        parent_dir = WORKSPACES_DIR / tree_id / (parent_id or root_id)
        if parent_dir.exists():
            _, parent_commit, _ = await _run_git(parent_dir, "rev-parse", "HEAD")
        else:
            # Parent worktree removed — resolve from branch ref in main repo
            parent_branch = f"ct-{parent_id}" if parent_id and parent_id != root_id else "HEAD"
            _, parent_commit, _ = await _run_git(main_repo, "rev-parse", parent_branch)

    return await create_worktree(tree_id, root_id, node_id, parent_commit)


async def remove_worktree(tree_id: str, root_id: str, node_id: str) -> bool:
    """Remove a node's worktree directory, keeping the branch ref.

    Skips root nodes (the main repo). Returns True if removal happened.
    """
    if node_id == root_id:
        return False

    worktree_path = WORKSPACES_DIR / tree_id / node_id
    if not worktree_path.exists():
        return False

    main_repo = WORKSPACES_DIR / tree_id / root_id
    try:
        await _run_git(main_repo, "worktree", "remove", "--force", str(worktree_path))
    except Exception:
        # Fallback: manual removal + prune
        try:
            shutil.rmtree(worktree_path, ignore_errors=True)
            await _run_git(main_repo, "worktree", "prune", check=False)
        except Exception as e2:
            log.warning("Worktree removal fallback failed: %s", e2)
            return False

    log.info("Removed worktree for node %s", node_id)
    return True


# ── Git-based reading (no worktree needed) ───────────────────────────

async def list_files_from_commit(tree_id: str, root_id: str, commit: str) -> list[str]:
    """List files at a commit using git ls-tree (no worktree needed)."""
    main_repo = WORKSPACES_DIR / tree_id / root_id
    _, out, _ = await _run_git(main_repo, "ls-tree", "-r", "--name-only", commit)
    return [f for f in out.splitlines() if f] if out else []


async def read_file_from_commit(tree_id: str, root_id: str, commit: str, file_path: str) -> str:
    """Read a file at a commit using git show (no worktree needed)."""
    main_repo = WORKSPACES_DIR / tree_id / root_id
    _, content, _ = await _run_git(main_repo, "show", f"{commit}:{file_path}")
    return content


async def get_diff_from_commits(tree_id: str, root_id: str, parent_commit: str | None, commit: str) -> str:
    """Get diff between two commits (no worktree needed)."""
    main_repo = WORKSPACES_DIR / tree_id / root_id
    if parent_commit:
        _, diff, _ = await _run_git(main_repo, "diff", parent_commit, commit, check=False)
    else:
        _, diff, _ = await _run_git(
            main_repo, "diff", "4b825dc642cb6eb9a060e54bf899d15006578e83", commit, check=False
        )
    return diff


# ── Auto-commit ───────────────────────────────────────────────────────

async def auto_commit(worktree_path: Path, message: str) -> tuple[str, int]:
    """Stage all changes and commit. Returns (HEAD sha, files_changed)."""
    await _run_git(worktree_path, "add", "-A")

    # Check if there are staged changes
    rc, _, _ = await _run_git(worktree_path, "diff", "--cached", "--quiet", check=False)
    files_changed = 0

    if rc != 0:
        # Count changed files before committing
        _, diff_names, _ = await _run_git(worktree_path, "diff", "--cached", "--name-only")
        files_changed = len(diff_names.splitlines()) if diff_names else 0

        # Commit
        commit_msg = f"ct: {message[:72]}"
        await _run_git(worktree_path, "commit", "-m", commit_msg)

    # Return current HEAD regardless
    _, sha, _ = await _run_git(worktree_path, "rev-parse", "HEAD")
    return sha, files_changed


# ── Workspace resolution ─────────────────────────────────────────────

def resolve_workspace(tree_id: str, root_id: str, node_id: str) -> Path:
    """Return the workspace path for a node (per-node directory)."""
    return WORKSPACES_DIR / tree_id / node_id


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
    return [f for f in out.splitlines() if f] if out else []


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


# ── Cleanup ───────────────────────────────────────────────────────────

def cleanup_tree_workspace(tree_id: str):
    """Kill any running processes and remove the workspace directory for a tree."""
    tree_dir = WORKSPACES_DIR / tree_id
    if tree_dir.exists():
        from services.process_service import kill_all_in_workspace
        kill_all_in_workspace(tree_dir)
        shutil.rmtree(tree_dir, ignore_errors=True)
