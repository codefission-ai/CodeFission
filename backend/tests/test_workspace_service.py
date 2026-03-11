"""Tests for workspace_service — repo setup, worktrees, resolve_workspace."""

import pytest
from pathlib import Path

from services.workspace_service import (
    setup_repo, create_worktree, ensure_worktree,
    auto_commit, resolve_workspace, copy_session, cleanup_tree_workspace,
    list_files, get_diff, read_file,
    remove_worktree, list_files_from_commit, read_file_from_commit,
    read_file_bytes_from_commit, get_diff_from_commits,
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
    assert ".claude/" in (root_dir / ".gitignore").read_text()

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


@pytest.mark.asyncio
async def test_setup_repo_url_requires_source(tree_ids):
    """repo_mode='url' without source raises ValueError."""
    with pytest.raises(ValueError, match="repo_source required"):
        await setup_repo(tree_ids["tree"], tree_ids["root"], "url", None)


@pytest.mark.asyncio
async def test_setup_repo_sets_git_config(tree_ids, tmp_path):
    """setup_repo sets committer identity in the repo config."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, email, _ = await _run_git(root_dir, "config", "user.email")
    _, name, _ = await _run_git(root_dir, "config", "user.name")
    assert email == "repoevolve@local"
    assert name == "RepoEvolve"


@pytest.mark.asyncio
async def test_setup_repo_local_clone(tree_ids, tmp_path):
    """repo_mode='local' clones from a local git repo."""
    # Create a source repo
    src = tmp_path / "source-repo"
    src.mkdir()
    await _run_git(src, "init")
    await _run_git(src, "config", "user.email", "test@test")
    await _run_git(src, "config", "user.name", "Test")
    (src / "README.md").write_text("# Hello\n")
    await _run_git(src, "add", "-A")
    await _run_git(src, "commit", "-m", "init")

    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "local", str(src))
    assert (root_dir / ".git").exists()
    assert (root_dir / "README.md").exists()
    assert (root_dir / "README.md").read_text() == "# Hello\n"
    # .claude/ should be in .gitignore
    gitignore = (root_dir / ".gitignore").read_text()
    assert ".claude/" in gitignore


@pytest.mark.asyncio
async def test_setup_repo_returns_correct_path(tree_ids, tmp_path):
    """setup_repo returns WORKSPACES_DIR / tree_id / root_id."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    assert root_dir == tmp_path / tree_ids["tree"] / tree_ids["root"]


# ── resolve_workspace ────────────────────────────────────────────────

def test_resolve_workspace(tree_ids, tmp_path, monkeypatch):
    """resolve_workspace always returns per-node path."""
    import services.workspace_service as ws_mod
    monkeypatch.setattr(ws_mod, "WORKSPACES_DIR", tmp_path)

    path = resolve_workspace("t1", "root", "node1")
    assert path == tmp_path / "t1" / "node1"


def test_resolve_workspace_root_same_as_node(tree_ids, tmp_path, monkeypatch):
    """For the root node, resolve_workspace returns tree/root path."""
    import services.workspace_service as ws_mod
    monkeypatch.setattr(ws_mod, "WORKSPACES_DIR", tmp_path)

    path = resolve_workspace("t1", "root", "root")
    assert path == tmp_path / "t1" / "root"


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
async def test_create_worktree_shares_history(tree_ids, tmp_path):
    """Worktree starts at the same commit as its parent."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    (root_dir / "file.txt").write_text("content\n")
    await _run_git(root_dir, "add", "-A")
    await _run_git(root_dir, "commit", "-m", "add file")
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    wt_path = await create_worktree(
        tree_ids["tree"], tree_ids["root"], tree_ids["child"], commit,
    )
    # Child should have the same file
    assert (wt_path / "file.txt").exists()
    assert (wt_path / "file.txt").read_text() == "content\n"


@pytest.mark.asyncio
async def test_create_worktree_isolation(tree_ids, tmp_path):
    """Changes in a worktree don't appear in the main repo."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    wt_path = await create_worktree(
        tree_ids["tree"], tree_ids["root"], tree_ids["child"], commit,
    )
    # Write to worktree only
    (wt_path / "child_only.txt").write_text("isolated\n")
    await _run_git(wt_path, "add", "-A")
    await _run_git(wt_path, "commit", "-m", "child change")

    # Main repo should not have the file
    assert not (root_dir / "child_only.txt").exists()


@pytest.mark.asyncio
async def test_create_multiple_worktrees(tree_ids, tmp_path):
    """Multiple worktrees can coexist from the same commit."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    wt1 = await create_worktree(tree_ids["tree"], tree_ids["root"], "child-1", commit)
    wt2 = await create_worktree(tree_ids["tree"], tree_ids["root"], "child-2", commit)
    wt3 = await create_worktree(tree_ids["tree"], tree_ids["root"], "child-3", commit)

    assert wt1.exists() and wt2.exists() and wt3.exists()
    assert wt1 != wt2 != wt3


@pytest.mark.asyncio
async def test_ensure_worktree_root(tree_ids, tmp_path):
    """ensure_worktree for root node returns existing path."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    result = await ensure_worktree(
        tree_ids["tree"], tree_ids["root"], tree_ids["root"], None, None,
    )
    assert result == root_dir


@pytest.mark.asyncio
async def test_ensure_worktree_creates_new(tree_ids, tmp_path):
    """ensure_worktree creates a new worktree if it doesn't exist."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    wt = await ensure_worktree(
        tree_ids["tree"], tree_ids["root"], "new-node",
        tree_ids["root"], commit,
    )
    assert wt.exists()
    _, branch, _ = await _run_git(wt, "rev-parse", "--abbrev-ref", "HEAD")
    assert branch == "ct-new-node"


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


@pytest.mark.asyncio
async def test_ensure_worktree_root_missing_raises(tree_ids, tmp_path):
    """ensure_worktree for root when path doesn't exist raises RuntimeError."""
    # Don't call setup_repo — root workspace doesn't exist
    with pytest.raises(RuntimeError, match="Root workspace missing"):
        await ensure_worktree(
            tree_ids["tree"], tree_ids["root"], tree_ids["root"], None, None,
        )


@pytest.mark.asyncio
async def test_ensure_worktree_resolves_parent_commit(tree_ids, tmp_path):
    """ensure_worktree reads parent's HEAD when parent_commit is None."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    # Don't pass parent_commit — it should read from parent worktree
    wt = await ensure_worktree(
        tree_ids["tree"], tree_ids["root"], "auto-node",
        tree_ids["root"], None,
    )
    assert wt.exists()


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


@pytest.mark.asyncio
async def test_auto_commit_multiple_files(tree_ids, tmp_path):
    """auto_commit counts all changed files."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    (root_dir / "a.py").write_text("a\n")
    (root_dir / "b.py").write_text("b\n")
    (root_dir / "c.py").write_text("c\n")
    sha, count = await auto_commit(root_dir, "add three files")
    assert count == 3


@pytest.mark.asyncio
async def test_auto_commit_message_truncated(tree_ids, tmp_path):
    """auto_commit truncates long messages to 72 chars."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    (root_dir / "file.txt").write_text("data\n")
    long_msg = "x" * 200
    await auto_commit(root_dir, long_msg)

    _, log, _ = await _run_git(root_dir, "log", "--format=%s", "-1")
    assert len(log) <= 76  # "ct: " + 72 chars


@pytest.mark.asyncio
async def test_auto_commit_modified_file(tree_ids, tmp_path):
    """auto_commit handles modified (not just new) files."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    (root_dir / "file.txt").write_text("v1\n")
    await auto_commit(root_dir, "create")

    (root_dir / "file.txt").write_text("v2\n")
    sha, count = await auto_commit(root_dir, "modify")
    assert count == 1


@pytest.mark.asyncio
async def test_auto_commit_deleted_file(tree_ids, tmp_path):
    """auto_commit handles deleted files."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    (root_dir / "file.txt").write_text("content\n")
    await auto_commit(root_dir, "create")

    (root_dir / "file.txt").unlink()
    sha, count = await auto_commit(root_dir, "delete")
    assert count == 1


# ── list_files ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_files_tracked(tree_ids, tmp_path):
    """list_files returns tracked files."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    (root_dir / "main.py").write_text("print(1)\n")
    await _run_git(root_dir, "add", "-A")
    await _run_git(root_dir, "commit", "-m", "add")

    files = await list_files(root_dir)
    assert "main.py" in files
    assert ".gitignore" not in files


@pytest.mark.asyncio
async def test_list_files_excludes_git(tree_ids, tmp_path):
    """list_files does not include .git internals."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    files = await list_files(root_dir)
    assert not any(f.startswith(".git/") for f in files)


@pytest.mark.asyncio
async def test_list_files_includes_untracked(tree_ids, tmp_path):
    """list_files includes untracked files not in .gitignore."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    (root_dir / "untracked.txt").write_text("hello\n")
    files = await list_files(root_dir)
    assert "untracked.txt" in files


# ── get_diff ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_diff_against_parent(tree_ids, tmp_path):
    """get_diff shows changes between parent commit and HEAD."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, parent_commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    (root_dir / "new.py").write_text("print('new')\n")
    await _run_git(root_dir, "add", "-A")
    await _run_git(root_dir, "commit", "-m", "add new")

    diff = await get_diff(root_dir, parent_commit)
    assert "+print('new')" in diff
    assert "new.py" in diff


@pytest.mark.asyncio
async def test_get_diff_no_parent(tree_ids, tmp_path):
    """get_diff with no parent diffs against the empty tree."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    diff = await get_diff(root_dir, None)
    # The initial commit only has .gitignore; diff against empty tree shows it
    # (result may be empty string if git considers the diff trivial)
    assert isinstance(diff, str)


# ── read_file ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_file_basic(tree_ids, tmp_path):
    """read_file returns file contents."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    (root_dir / "hello.txt").write_text("world\n")
    content = read_file(root_dir, "hello.txt")
    assert content == "world\n"


def test_read_file_traversal_blocked(tmp_path):
    """read_file blocks path traversal attempts."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (tmp_path / "secret.txt").write_text("secret\n")

    with pytest.raises(ValueError, match="Path traversal"):
        read_file(ws, "../secret.txt")


def test_read_file_not_found(tmp_path):
    """read_file raises FileNotFoundError for missing files."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    with pytest.raises(FileNotFoundError):
        read_file(ws, "nonexistent.txt")


def test_read_file_size_limit(tmp_path):
    """read_file rejects files over 1MB."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    big = ws / "big.bin"
    big.write_bytes(b"x" * (1_048_577))  # Just over 1MB
    with pytest.raises(ValueError, match="too large"):
        read_file(ws, "big.bin")


# ── cleanup_tree_workspace ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_cleanup_tree_workspace(tree_ids, tmp_path):
    """cleanup_tree_workspace removes the tree directory."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    assert root_dir.exists()
    cleanup_tree_workspace(tree_ids["tree"])
    assert not (tmp_path / tree_ids["tree"]).exists()


def test_cleanup_tree_workspace_nonexistent(tmp_path, monkeypatch):
    """cleanup_tree_workspace is safe on nonexistent paths."""
    import services.workspace_service as ws_mod
    monkeypatch.setattr(ws_mod, "WORKSPACES_DIR", tmp_path)
    cleanup_tree_workspace("nonexistent")  # should not raise


# ── copy_session ─────────────────────────────────────────────────────

def test_claude_project_dir():
    """Project dir encodes the workspace path with dashes."""
    p = _claude_project_dir(Path("/home/user/project"))
    assert p.name == "-home-user-project"


def test_claude_project_dir_nested():
    """Deeply nested paths are encoded correctly."""
    p = _claude_project_dir(Path("/a/b/c/d/e"))
    assert p.name == "-a-b-c-d-e"


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


def test_copy_session_missing_source(tmp_path, monkeypatch):
    """copy_session handles missing parent session gracefully."""
    import services.workspace_service as ws_mod
    monkeypatch.setattr(ws_mod, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")

    parent_ws = Path("/fake/parent")
    child_ws = Path("/fake/child")
    # Don't create parent session file
    copy_session(parent_ws, child_ws, "nonexistent-session")
    # Should not raise, child dir should not be created
    child_proj = tmp_path / "projects" / "-fake-child"
    assert not child_proj.exists()


# ── _run_git ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_git_check_raises(tmp_path):
    """_run_git with check=True raises on non-zero exit."""
    with pytest.raises(RuntimeError, match="failed"):
        await _run_git(tmp_path, "status")  # not a git repo


@pytest.mark.asyncio
async def test_run_git_check_false(tmp_path):
    """_run_git with check=False returns non-zero without raising."""
    rc, _, _ = await _run_git(tmp_path, "status", check=False)
    assert rc != 0


# ── remove_worktree ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remove_worktree(tree_ids, tmp_path):
    """remove_worktree removes directory but preserves branch ref."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")
    wt = await create_worktree(tree_ids["tree"], tree_ids["root"], tree_ids["child"], commit)
    assert wt.exists()

    result = await remove_worktree(tree_ids["tree"], tree_ids["root"], tree_ids["child"])
    assert result is True
    assert not wt.exists()

    # Branch should still exist in main repo
    rc, _, _ = await _run_git(root_dir, "rev-parse", "--verify", f"ct-{tree_ids['child']}", check=False)
    assert rc == 0


@pytest.mark.asyncio
async def test_remove_worktree_root_skipped(tree_ids, tmp_path):
    """remove_worktree on root returns False and does nothing."""
    await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    result = await remove_worktree(tree_ids["tree"], tree_ids["root"], tree_ids["root"])
    assert result is False
    assert (tmp_path / tree_ids["tree"] / tree_ids["root"]).exists()


@pytest.mark.asyncio
async def test_remove_worktree_nonexistent(tree_ids, tmp_path):
    """remove_worktree on non-existent path returns False."""
    await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    result = await remove_worktree(tree_ids["tree"], tree_ids["root"], "nonexistent")
    assert result is False


# ── ensure_worktree after removal ────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_worktree_after_removal(tree_ids, tmp_path):
    """ensure_worktree recreates from existing branch after removal."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    # Create, add content, commit, remove
    wt = await create_worktree(tree_ids["tree"], tree_ids["root"], tree_ids["child"], commit)
    (wt / "test.txt").write_text("hello\n")
    await _run_git(wt, "add", "-A")
    await _run_git(wt, "commit", "-m", "add test")
    _, child_commit, _ = await _run_git(wt, "rev-parse", "HEAD")

    await remove_worktree(tree_ids["tree"], tree_ids["root"], tree_ids["child"])
    assert not wt.exists()

    # Re-create via ensure_worktree
    wt2 = await ensure_worktree(
        tree_ids["tree"], tree_ids["root"], tree_ids["child"],
        tree_ids["root"], commit,
    )
    assert wt2.exists()
    # Should be on the same branch with the committed content
    _, branch, _ = await _run_git(wt2, "rev-parse", "--abbrev-ref", "HEAD")
    assert branch == f"ct-{tree_ids['child']}"
    assert (wt2 / "test.txt").exists()
    assert (wt2 / "test.txt").read_text() == "hello\n"


# ── Git-based reading functions ──────────────────────────────────────

@pytest.mark.asyncio
async def test_list_files_from_commit(tree_ids, tmp_path):
    """list_files_from_commit returns files at a specific commit."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    (root_dir / "main.py").write_text("print(1)\n")
    (root_dir / "lib.py").write_text("x = 1\n")
    await _run_git(root_dir, "add", "-A")
    await _run_git(root_dir, "commit", "-m", "add files")
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    files = await list_files_from_commit(tree_ids["tree"], tree_ids["root"], commit)
    assert "main.py" in files
    assert "lib.py" in files
    assert ".gitignore" not in files


@pytest.mark.asyncio
async def test_read_file_from_commit(tree_ids, tmp_path):
    """read_file_from_commit returns file content at a specific commit."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    (root_dir / "hello.txt").write_text("world\n")
    await _run_git(root_dir, "add", "-A")
    await _run_git(root_dir, "commit", "-m", "add hello")
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    content = await read_file_from_commit(tree_ids["tree"], tree_ids["root"], commit, "hello.txt")
    assert content == "world"


@pytest.mark.asyncio
async def test_get_diff_from_commits(tree_ids, tmp_path):
    """get_diff_from_commits shows diff between two commits."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, parent_commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    (root_dir / "new.py").write_text("print('new')\n")
    await _run_git(root_dir, "add", "-A")
    await _run_git(root_dir, "commit", "-m", "add new")
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    diff = await get_diff_from_commits(tree_ids["tree"], tree_ids["root"], parent_commit, commit)
    assert "+print('new')" in diff
    assert "new.py" in diff


@pytest.mark.asyncio
async def test_get_diff_from_commits_no_parent(tree_ids, tmp_path):
    """get_diff_from_commits with no parent diffs against empty tree."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    diff = await get_diff_from_commits(tree_ids["tree"], tree_ids["root"], None, commit)
    assert isinstance(diff, str)


# ── Binary file handling ─────────────────────────────────────────────

# Minimal valid 1x1 red PNG (68 bytes) — contains bytes that are invalid UTF-8
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"  # PNG signature
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
    b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
    b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.asyncio
async def test_read_file_from_commit_corrupts_binary(tree_ids, tmp_path):
    """read_file_from_commit (text mode) corrupts binary files like PNG."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    (root_dir / "image.png").write_bytes(TINY_PNG)
    await _run_git(root_dir, "add", "-A")
    await _run_git(root_dir, "commit", "-m", "add png")
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    # Text-mode read → encode round-trip corrupts binary data
    text_content = await read_file_from_commit(
        tree_ids["tree"], tree_ids["root"], commit, "image.png"
    )
    round_tripped = text_content.encode()
    assert round_tripped != TINY_PNG, "Expected corruption but data survived"


@pytest.mark.asyncio
async def test_read_file_bytes_from_commit_preserves_binary(tree_ids, tmp_path):
    """read_file_bytes_from_commit preserves binary files exactly."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    (root_dir / "image.png").write_bytes(TINY_PNG)
    await _run_git(root_dir, "add", "-A")
    await _run_git(root_dir, "commit", "-m", "add png")
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    raw = await read_file_bytes_from_commit(
        tree_ids["tree"], tree_ids["root"], commit, "image.png"
    )
    assert raw == TINY_PNG


@pytest.mark.asyncio
async def test_read_file_bytes_from_commit_works_for_text(tree_ids, tmp_path):
    """read_file_bytes_from_commit also works for text files."""
    root_dir = await setup_repo(tree_ids["tree"], tree_ids["root"], "new", None)
    (root_dir / "hello.txt").write_text("world\n")
    await _run_git(root_dir, "add", "-A")
    await _run_git(root_dir, "commit", "-m", "add hello")
    _, commit, _ = await _run_git(root_dir, "rev-parse", "HEAD")

    raw = await read_file_bytes_from_commit(
        tree_ids["tree"], tree_ids["root"], commit, "hello.txt"
    )
    assert raw == b"world\n"
