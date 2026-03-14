"""Tests for workspace_service — worktrees, resolve_workspace, auto_commit.

These tests work against a temporary git project (not the user's real repo).
"""

import pytest
from pathlib import Path

from services.workspace import (
    create_worktree, ensure_worktree,
    auto_commit, resolve_workspace, copy_session,
    list_files, get_diff, read_file,
    remove_worktree, remove_worktree_and_branch,
    list_files_from_commit, read_file_from_commit,
    read_file_bytes_from_commit, get_diff_from_commits,
    cleanup_tree_workspaces, list_branches,
    _claude_project_dir, _run_git, _GIT_ENV,
)


# ── Helper: init a git repo with one commit ──────────────────────────

async def _init_project(project_path: Path):
    """Initialise the project dir as a git repo with one commit."""
    await _run_git(project_path, "init")
    await _run_git(project_path, "config", "user.email", "test@test")
    await _run_git(project_path, "config", "user.name", "Test")
    gitignore = project_path / ".gitignore"
    gitignore.write_text(".codefission/\n.claude/\n_artifacts/\n")
    await _run_git(project_path, "add", "-A")
    await _run_git(project_path, "commit", "-m", "initial commit", env=_GIT_ENV)


@pytest.fixture
def root_id():
    return "root-node"


@pytest.fixture
def child_id():
    return "child-node"


# ── resolve_workspace ────────────────────────────────────────────────

def test_resolve_workspace_root(tmp_project, root_id):
    """Root node resolves to PROJECT_PATH."""
    path = resolve_workspace(root_id, root_id)
    assert path == tmp_project


def test_resolve_workspace_child(tmp_project, root_id, child_id):
    """Child node resolves to worktrees dir / node_id."""
    from config import get_project_dir
    path = resolve_workspace(root_id, child_id)
    assert path == get_project_dir() / "worktrees" / child_id


# ── create_worktree ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_worktree(tmp_project, child_id):
    """create_worktree creates a git worktree branched from a commit."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")

    wt_path = await create_worktree(child_id, commit)
    assert wt_path.exists()
    _, branch, _ = await _run_git(wt_path, "rev-parse", "--abbrev-ref", "HEAD")
    assert branch == f"ct-{child_id}"


@pytest.mark.asyncio
async def test_create_worktree_shares_history(tmp_project, child_id):
    """Worktree starts at the same commit as its parent."""
    await _init_project(tmp_project)
    (tmp_project / "file.txt").write_text("content\n")
    await _run_git(tmp_project, "add", "-A")
    await _run_git(tmp_project, "commit", "-m", "add file", env=_GIT_ENV)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")

    wt_path = await create_worktree(child_id, commit)
    assert (wt_path / "file.txt").exists()
    assert (wt_path / "file.txt").read_text() == "content\n"


@pytest.mark.asyncio
async def test_create_worktree_isolation(tmp_project, root_id, child_id):
    """Changes in a worktree don't appear in the main repo."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")

    wt_path = await create_worktree(child_id, commit)
    (wt_path / "child_only.txt").write_text("isolated\n")
    await _run_git(wt_path, "add", "-A")
    await _run_git(wt_path, "commit", "-m", "child change", env=_GIT_ENV)

    assert not (tmp_project / "child_only.txt").exists()


@pytest.mark.asyncio
async def test_create_multiple_worktrees(tmp_project):
    """Multiple worktrees can coexist from the same commit."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")

    wt1 = await create_worktree("child-1", commit)
    wt2 = await create_worktree("child-2", commit)
    wt3 = await create_worktree("child-3", commit)

    assert wt1.exists() and wt2.exists() and wt3.exists()
    assert wt1 != wt2 != wt3


# ── ensure_worktree ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_worktree_root(tmp_project, root_id):
    """ensure_worktree for root node returns PROJECT_PATH."""
    await _init_project(tmp_project)
    result = await ensure_worktree(root_id, root_id, None, None)
    assert result == tmp_project


@pytest.mark.asyncio
async def test_ensure_worktree_creates_new(tmp_project, root_id):
    """ensure_worktree creates a new worktree if it doesn't exist."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")

    wt = await ensure_worktree(root_id, "new-node", root_id, commit)
    assert wt.exists()
    _, branch, _ = await _run_git(wt, "rev-parse", "--abbrev-ref", "HEAD")
    assert branch == "ct-new-node"


@pytest.mark.asyncio
async def test_ensure_worktree_idempotent(tmp_project, root_id, child_id):
    """ensure_worktree returns existing worktree without error."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")

    wt1 = await create_worktree(child_id, commit)
    wt2 = await ensure_worktree(root_id, child_id, root_id, commit)
    assert wt1 == wt2


@pytest.mark.asyncio
async def test_ensure_worktree_resolves_parent_commit(tmp_project, root_id):
    """ensure_worktree reads parent's HEAD when parent_commit is None."""
    await _init_project(tmp_project)
    wt = await ensure_worktree(root_id, "auto-node", root_id, None)
    assert wt.exists()


# ── auto_commit ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_commit_skips_project_path(tmp_project):
    """auto_commit on PROJECT_PATH returns current HEAD without committing."""
    await _init_project(tmp_project)
    (tmp_project / "new_file.txt").write_text("should not be committed\n")
    sha, count = await auto_commit(tmp_project, "test")
    assert len(sha) == 40
    assert count == 0
    # File should still be untracked
    rc, _, _ = await _run_git(tmp_project, "diff", "--cached", "--quiet", check=False)
    assert rc == 0  # nothing staged


@pytest.mark.asyncio
async def test_auto_commit_no_changes(tmp_project, root_id):
    """auto_commit with no changes returns current HEAD and 0 files."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("commit-test", commit)

    sha, count = await auto_commit(wt, "test")
    assert len(sha) == 40
    assert count == 0


@pytest.mark.asyncio
async def test_auto_commit_with_changes(tmp_project, root_id):
    """auto_commit stages and commits new files."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("commit-test2", commit)
    _, old_sha, _ = await _run_git(wt, "rev-parse", "HEAD")

    (wt / "hello.py").write_text("print('hi')\n")
    sha, count = await auto_commit(wt, "add hello")

    assert sha != old_sha
    assert count == 1


@pytest.mark.asyncio
async def test_auto_commit_multiple_files(tmp_project):
    """auto_commit counts all changed files."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("commit-multi", commit)

    (wt / "a.py").write_text("a\n")
    (wt / "b.py").write_text("b\n")
    (wt / "c.py").write_text("c\n")
    sha, count = await auto_commit(wt, "add three files")
    assert count == 3


@pytest.mark.asyncio
async def test_auto_commit_message_truncated(tmp_project):
    """auto_commit truncates long messages to 72 chars."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("commit-trunc", commit)

    (wt / "file.txt").write_text("data\n")
    long_msg = "x" * 200
    await auto_commit(wt, long_msg)

    _, log, _ = await _run_git(wt, "log", "--format=%s", "-1")
    assert len(log) <= 76  # "ct: " + 72 chars


@pytest.mark.asyncio
async def test_auto_commit_modified_file(tmp_project):
    """auto_commit handles modified (not just new) files."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("commit-modify", commit)

    (wt / "file.txt").write_text("v1\n")
    await auto_commit(wt, "create")

    (wt / "file.txt").write_text("v2\n")
    sha, count = await auto_commit(wt, "modify")
    assert count == 1


@pytest.mark.asyncio
async def test_auto_commit_deleted_file(tmp_project):
    """auto_commit handles deleted files."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("commit-delete", commit)

    (wt / "file.txt").write_text("content\n")
    await auto_commit(wt, "create")

    (wt / "file.txt").unlink()
    sha, count = await auto_commit(wt, "delete")
    assert count == 1


# ── list_files ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_files_tracked(tmp_project):
    """list_files returns tracked files."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("list-files", commit)

    (wt / "main.py").write_text("print(1)\n")
    await _run_git(wt, "add", "-A")
    await _run_git(wt, "commit", "-m", "add", env=_GIT_ENV)

    files = await list_files(wt)
    assert "main.py" in files
    assert ".gitignore" not in files


@pytest.mark.asyncio
async def test_list_files_excludes_git(tmp_project):
    """list_files does not include .git internals."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("list-git", commit)

    files = await list_files(wt)
    assert not any(f.startswith(".git/") for f in files)


@pytest.mark.asyncio
async def test_list_files_includes_untracked(tmp_project):
    """list_files includes untracked files not in .gitignore."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("list-untracked", commit)

    (wt / "untracked.txt").write_text("hello\n")
    files = await list_files(wt)
    assert "untracked.txt" in files


# ── get_diff ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_diff_against_parent(tmp_project):
    """get_diff shows changes between parent commit and HEAD."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("diff-test", commit)

    _, parent_commit, _ = await _run_git(wt, "rev-parse", "HEAD")
    (wt / "new.py").write_text("print('new')\n")
    await _run_git(wt, "add", "-A")
    await _run_git(wt, "commit", "-m", "add new", env=_GIT_ENV)

    diff = await get_diff(wt, parent_commit)
    assert "+print('new')" in diff
    assert "new.py" in diff


@pytest.mark.asyncio
async def test_get_diff_no_parent(tmp_project):
    """get_diff with no parent diffs against the empty tree."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("diff-nop", commit)

    diff = await get_diff(wt, None)
    assert isinstance(diff, str)


# ── read_file ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_file_basic(tmp_project):
    """read_file returns file contents."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("read-test", commit)

    (wt / "hello.txt").write_text("world\n")
    content = read_file(wt, "hello.txt")
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


# ── remove_worktree ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remove_worktree(tmp_project, root_id, child_id):
    """remove_worktree removes directory but preserves branch ref."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree(child_id, commit)
    assert wt.exists()

    result = await remove_worktree(root_id, child_id)
    assert result is True
    assert not wt.exists()

    # Branch should still exist in main repo
    rc, _, _ = await _run_git(tmp_project, "rev-parse", "--verify", f"ct-{child_id}", check=False)
    assert rc == 0


@pytest.mark.asyncio
async def test_remove_worktree_root_skipped(tmp_project, root_id):
    """remove_worktree on root returns False and does nothing."""
    await _init_project(tmp_project)
    result = await remove_worktree(root_id, root_id)
    assert result is False
    assert tmp_project.exists()


@pytest.mark.asyncio
async def test_remove_worktree_nonexistent(tmp_project, root_id):
    """remove_worktree on non-existent path returns False."""
    await _init_project(tmp_project)
    result = await remove_worktree(root_id, "nonexistent")
    assert result is False


# ── remove_worktree_and_branch ───────────────────────────────────────

@pytest.mark.asyncio
async def test_remove_worktree_and_branch(tmp_project, root_id, child_id):
    """remove_worktree_and_branch removes both directory and branch ref."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree(child_id, commit)
    assert wt.exists()

    result = await remove_worktree_and_branch(root_id, child_id)
    assert result is True
    assert not wt.exists()

    # Branch should be deleted
    rc, _, _ = await _run_git(tmp_project, "rev-parse", "--verify", f"ct-{child_id}", check=False)
    assert rc != 0


# ── ensure_worktree after removal ────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_worktree_after_removal(tmp_project, root_id, child_id):
    """ensure_worktree recreates from existing branch after removal."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")

    # Create, add content, commit, remove (keep branch)
    wt = await create_worktree(child_id, commit)
    (wt / "test.txt").write_text("hello\n")
    await _run_git(wt, "add", "-A")
    await _run_git(wt, "commit", "-m", "add test", env=_GIT_ENV)

    await remove_worktree(root_id, child_id)
    assert not wt.exists()

    # Re-create via ensure_worktree
    wt2 = await ensure_worktree(root_id, child_id, root_id, commit)
    assert wt2.exists()
    _, branch, _ = await _run_git(wt2, "rev-parse", "--abbrev-ref", "HEAD")
    assert branch == f"ct-{child_id}"
    assert (wt2 / "test.txt").exists()
    assert (wt2 / "test.txt").read_text() == "hello\n"


# ── cleanup_tree_workspaces ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_cleanup_tree_workspaces(tmp_project, root_id, child_id):
    """cleanup_tree_workspaces removes worktrees and branches for listed nodes."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree(child_id, commit)
    assert wt.exists()

    cleanup_tree_workspaces(tmp_project, root_id, [root_id, child_id])
    assert not wt.exists()
    assert tmp_project.exists()  # root (PROJECT_PATH) is never touched


def test_cleanup_tree_workspaces_nonexistent(tmp_project, root_id):
    """cleanup_tree_workspaces is safe on nonexistent nodes."""
    cleanup_tree_workspaces(tmp_project, root_id, ["nonexistent"])  # should not raise


# ── Git-based reading functions ──────────────────────────────────────

@pytest.mark.asyncio
async def test_list_files_from_commit(tmp_project):
    """list_files_from_commit returns files at a specific commit."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("list-commit", commit)

    (wt / "main.py").write_text("print(1)\n")
    (wt / "lib.py").write_text("x = 1\n")
    await _run_git(wt, "add", "-A")
    await _run_git(wt, "commit", "-m", "add files", env=_GIT_ENV)
    _, wt_commit, _ = await _run_git(wt, "rev-parse", "HEAD")

    files = await list_files_from_commit(wt_commit)
    assert "main.py" in files
    assert "lib.py" in files
    assert ".gitignore" not in files


@pytest.mark.asyncio
async def test_read_file_from_commit(tmp_project):
    """read_file_from_commit returns file content at a specific commit."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("read-commit", commit)

    (wt / "hello.txt").write_text("world\n")
    await _run_git(wt, "add", "-A")
    await _run_git(wt, "commit", "-m", "add hello", env=_GIT_ENV)
    _, wt_commit, _ = await _run_git(wt, "rev-parse", "HEAD")

    content = await read_file_from_commit(wt_commit, "hello.txt")
    assert content == "world"


@pytest.mark.asyncio
async def test_get_diff_from_commits(tmp_project):
    """get_diff_from_commits shows diff between two commits."""
    await _init_project(tmp_project)
    _, parent_commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("diff-commits", parent_commit)

    (wt / "new.py").write_text("print('new')\n")
    await _run_git(wt, "add", "-A")
    await _run_git(wt, "commit", "-m", "add new", env=_GIT_ENV)
    _, wt_commit, _ = await _run_git(wt, "rev-parse", "HEAD")

    diff = await get_diff_from_commits(parent_commit, wt_commit)
    assert "+print('new')" in diff
    assert "new.py" in diff


@pytest.mark.asyncio
async def test_get_diff_from_commits_no_parent(tmp_project):
    """get_diff_from_commits with no parent diffs against empty tree."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")

    diff = await get_diff_from_commits(None, commit)
    assert isinstance(diff, str)


# ── Binary file handling ─────────────────────────────────────────────

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
    b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
    b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.asyncio
async def test_read_file_from_commit_corrupts_binary(tmp_project):
    """read_file_from_commit (text mode) corrupts binary files like PNG."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("bin-corrupt", commit)

    (wt / "image.png").write_bytes(TINY_PNG)
    await _run_git(wt, "add", "-A")
    await _run_git(wt, "commit", "-m", "add png", env=_GIT_ENV)
    _, wt_commit, _ = await _run_git(wt, "rev-parse", "HEAD")

    text_content = await read_file_from_commit(wt_commit, "image.png")
    round_tripped = text_content.encode()
    assert round_tripped != TINY_PNG, "Expected corruption but data survived"


@pytest.mark.asyncio
async def test_read_file_bytes_from_commit_preserves_binary(tmp_project):
    """read_file_bytes_from_commit preserves binary files exactly."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("bin-preserve", commit)

    (wt / "image.png").write_bytes(TINY_PNG)
    await _run_git(wt, "add", "-A")
    await _run_git(wt, "commit", "-m", "add png", env=_GIT_ENV)
    _, wt_commit, _ = await _run_git(wt, "rev-parse", "HEAD")

    raw = await read_file_bytes_from_commit(wt_commit, "image.png")
    assert raw == TINY_PNG


@pytest.mark.asyncio
async def test_read_file_bytes_from_commit_works_for_text(tmp_project):
    """read_file_bytes_from_commit also works for text files."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")
    wt = await create_worktree("bin-text", commit)

    (wt / "hello.txt").write_text("world\n")
    await _run_git(wt, "add", "-A")
    await _run_git(wt, "commit", "-m", "add hello", env=_GIT_ENV)
    _, wt_commit, _ = await _run_git(wt, "rev-parse", "HEAD")

    raw = await read_file_bytes_from_commit(wt_commit, "hello.txt")
    assert raw == b"world\n"


# ── list_branches ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_branches(tmp_project):
    """list_branches returns branches, filtering out ct-* branches."""
    await _init_project(tmp_project)
    _, commit, _ = await _run_git(tmp_project, "rev-parse", "HEAD")

    # Create a ct- branch (should be excluded) and a user branch
    await create_worktree("some-node", commit)
    await _run_git(tmp_project, "branch", "feature-x", commit)

    branches = await list_branches()
    names = [b["name"] for b in branches]
    assert "feature-x" in names
    assert not any(n.startswith("ct-") for n in names)
    # Current branch should be marked
    current = [b for b in branches if b["current"]]
    assert len(current) == 1


# ── copy_session ─────────────────────────────────────────────────────

def test_claude_project_dir(tmp_path):
    """Project dir encodes the workspace path with dashes."""
    ws = tmp_path / "myproject"
    ws.mkdir()
    p = _claude_project_dir(ws)
    # The name should be the resolved path with / and . replaced by dashes
    expected_name = str(ws.resolve()).replace("/", "-").replace(".", "-")
    assert p.name == expected_name


def test_claude_project_dir_nested(tmp_path):
    """Deeply nested paths are encoded correctly."""
    ws = tmp_path / "a" / "b" / "c"
    ws.mkdir(parents=True)
    p = _claude_project_dir(ws)
    expected_name = str(ws.resolve()).replace("/", "-").replace(".", "-")
    assert p.name == expected_name


def test_copy_session(tmp_path, monkeypatch):
    """copy_session copies session file from parent to child project dir."""
    import services.workspace as ws_mod
    monkeypatch.setattr(ws_mod, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")

    parent_ws = Path("/fake/parent")
    child_ws = Path("/fake/child")
    session_id = "test-session-123"

    parent_proj = tmp_path / "projects" / "-fake-parent"
    parent_proj.mkdir(parents=True)
    (parent_proj / f"{session_id}.jsonl").write_text('{"test": true}\n')

    copy_session(parent_ws, child_ws, session_id)

    child_proj = tmp_path / "projects" / "-fake-child"
    assert (child_proj / f"{session_id}.jsonl").exists()
    assert (child_proj / f"{session_id}.jsonl").read_text() == '{"test": true}\n'


def test_copy_session_idempotent(tmp_path, monkeypatch):
    """copy_session doesn't overwrite existing session file."""
    import services.workspace as ws_mod
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
    assert (child_proj / f"{session_id}.jsonl").read_text() == "modified\n"


def test_copy_session_missing_source(tmp_path, monkeypatch):
    """copy_session handles missing parent session gracefully."""
    import services.workspace as ws_mod
    monkeypatch.setattr(ws_mod, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")

    parent_ws = Path("/fake/parent")
    child_ws = Path("/fake/child")
    copy_session(parent_ws, child_ws, "nonexistent-session")
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
