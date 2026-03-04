"""Tests for workspace_service — repo setup, worktrees, resolve_workspace."""

import pytest
from pathlib import Path

from services.workspace_service import (
    setup_repo, create_worktree, ensure_worktree,
    auto_commit, resolve_workspace, copy_session,
    _claude_project_dir, _run_git, WORKSPACES_DIR,
)


@pytest.fixture
def tree_ids(tmp_path, monkeypatch):
    """Provide fresh tree/node IDs and redirect WORKSPACES_DIR to tmp."""
    import services.workspace_service as ws_mod
    monkeypatch.setattr(ws_mod, "WORKSPACES_DIR", tmp_path)
    return {"tree": "test-tree", "root": "root-node", "child": "child-node"}


# ── setup_repo ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_setup_repo_new(tree_ids, tmp_path):
    """setup_repo('new') creates a git repo with an initial commit."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    assert (root_dir / ".git").exists()
    assert (root_dir / ".gitignore").exists()

    # Has at least one commit
    rc, sha, _ = await _run_git(root_dir, "rev-parse", "HEAD")
    assert rc == 0
    assert len(sha) == 40


@pytest.mark.asyncio
async def test_setup_repo_idempotent(tree_ids, tmp_path):
    """Calling setup_repo twice returns early without error."""
    root_dir1 = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, sha1, _ = await _run_git(root_dir1, "rev-parse", "HEAD")

    root_dir2 = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, sha2, _ = await _run_git(root_dir2, "rev-parse", "HEAD")

    assert root_dir1 == root_dir2
    assert sha1 == sha2  # no extra commit created


@pytest.mark.asyncio
async def test_setup_repo_unknown_mode(tree_ids):
    """Unknown repo_mode raises ValueError."""
    with pytest.raises(ValueError, match="Unknown repo_mode"):
        await setup_repo(tree_ids["tree"], tree_ids["root"], "bogus", None)


@pytest.mark.asyncio
async def test_setup_repo_local_requires_source(tree_ids):
    """repo_mode='local' without source raises ValueError."""
    with pytest.raises(ValueError, match="repo_source required"):
        await setup_repo(tree_ids["tree"], tree_ids["root"], "local", None)


# ── resolve_workspace ────────────────────────────────────────────────

def test_resolve_workspace(tree_ids, tmp_path, monkeypatch):
    """resolve_workspace always returns per-node path."""
    import services.workspace_service as ws_mod
    monkeypatch.setattr(ws_mod, "WORKSPACES_DIR", tmp_path)

    path = resolve_workspace("t1", "root", "node1")
    assert path == tmp_path / "t1" / "node1"


# ── create_worktree / ensure_worktree ────────────────────────────────

@pytest.mark.asyncio
async def test_create_worktree(tree_ids, tmp_path):
    """create_worktree creates a git worktree branched from a commit."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    wt_path = await create_worktree(
        tree_ids["tree"], tree_ids["root"], tree_ids["child"], commit,
    )
    assert wt_path.exists()
    # Worktree has its own branch
    _, branch, _ = await _run_git(wt_path, "rev-parse", "--abbrev-ref", "HEAD")
    assert branch == f"ct-{tree_ids['child']}"


@pytest.mark.asyncio
async def test_ensure_worktree_root(tree_ids, tmp_path):
    """ensure_worktree for root node returns existing path."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    result = await ensure_worktree(
        tree_ids["tree"], tree_ids["root"], tree_ids["root"], None, None,
    )
    assert result == root_dir


@pytest.mark.asyncio
async def test_ensure_worktree_idempotent(tree_ids, tmp_path):
    """ensure_worktree returns existing worktree without error."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    wt1 = await create_worktree(
        tree_ids["tree"], tree_ids["root"], tree_ids["child"], commit,
    )
    wt2 = await ensure_worktree(
        tree_ids["tree"], tree_ids["root"], tree_ids["child"],
        tree_ids["root"], commit,
    )
    assert wt1 == wt2


# ── auto_commit ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_commit_no_changes(tree_ids, tmp_path):
    """auto_commit with no changes returns current HEAD and 0 files."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    sha, count = await auto_commit(root_dir, "test")
    assert len(sha) == 40
    assert count == 0


@pytest.mark.asyncio
async def test_auto_commit_with_changes(tree_ids, tmp_path):
    """auto_commit stages and commits new files."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, old_sha, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    (root_dir / "hello.py").write_text("print('hi')\n")
    sha, count = await auto_commit(root_dir, "add hello")

    assert sha != old_sha
    assert count == 1


# ── copy_session ─────────────────────────────────────────────────────

def test_claude_project_dir():
    """Project dir encodes the workspace path with dashes."""
    p = _claude_project_dir(Path("/home/user/project"))
    assert p.name == "-home-user-project"


def test_copy_session(tmp_path, monkeypatch):
    """copy_session copies session file from parent to child project dir."""
    import services.workspace_service as ws_mod
    monkeypatch.setattr(ws_mod, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")

    parent_ws = Path("/fake/parent")
    child_ws = Path("/fake/child")
    session_id = "test-session-123"

    # Create parent session file
    parent_proj = tmp_path / "projects" / "-fake-parent"
    parent_proj.mkdir(parents=True)
    (parent_proj / f"{session_id}.jsonl").write_text('{"test": true}\n')

    copy_session(parent_ws, child_ws, session_id)

    child_proj = tmp_path / "projects" / "-fake-child"
    assert (child_proj / f"{session_id}.jsonl").exists()
    assert (child_proj / f"{session_id}.jsonl").read_text() == '{"test": true}\n'


def test_copy_session_idempotent(tmp_path, monkeypatch):
    """copy_session doesn't overwrite existing session file."""
    import services.workspace_service as ws_mod
    monkeypatch.setattr(ws_mod, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")

    parent_ws = Path("/fake/parent")
    child_ws = Path("/fake/child")
    session_id = "test-session-456"

    parent_proj = tmp_path / "projects" / "-fake-parent"
    parent_proj.mkdir(parents=True)
    (parent_proj / f"{session_id}.jsonl").write_text("original\n")

    child_proj = tmp_path / "projects" / "-fake-child"
    child_proj.mkdir(parents=True)
    (child_proj / f"{session_id}.jsonl").write_text("modified\n")

    copy_session(parent_ws, child_ws, session_id)

    # Should NOT overwrite
    assert (child_proj / f"{session_id}.jsonl").read_text() == "modified\n"
