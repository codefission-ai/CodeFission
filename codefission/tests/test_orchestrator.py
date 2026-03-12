"""Integration tests for the Orchestrator — end-to-end business logic without WebSocket.

These tests exercise create_tree → branch → prepare_chat → complete_chat (and
cancel/fail variants) against a real SQLite DB and real git repos in temp dirs.
No mocks except where noted — this validates the full data flow.
"""

import pytest

from services.orchestrator import Orchestrator, ChatContext, ChatResult, CancelResult
from services.tree_service import get_node, get_tree, update_node, set_setting
from services.workspace_service import _run_git


@pytest.fixture
def orch(tmp_db, tmp_workspaces, monkeypatch):
    """Orchestrator with temp DB and temp workspace directory."""
    import services.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "WORKSPACES_DIR", tmp_workspaces)
    return Orchestrator()


# ── create_tree ──────────────────────────────────────────────────────


class TestCreateTree:

    @pytest.mark.asyncio
    async def test_returns_tree_and_root(self, orch):
        tree, root = await orch.create_tree("My Tree")
        assert tree.name == "My Tree"
        assert tree.repo_mode == "new"
        assert root.tree_id == tree.id
        assert root.parent_id is None
        assert root.label == "root"

    @pytest.mark.asyncio
    async def test_root_has_git_metadata(self, orch):
        tree, root = await orch.create_tree("Git Tree")
        assert root.git_branch is not None
        assert root.git_commit is not None
        assert len(root.git_commit) == 40  # full SHA

    @pytest.mark.asyncio
    async def test_git_repo_initialized(self, orch, tmp_workspaces):
        tree, root = await orch.create_tree("Repo Tree")
        root_dir = tmp_workspaces / tree.id / root.id
        assert (root_dir / ".git").exists()
        rc, _, _ = await _run_git(root_dir, "status", check=False)
        assert rc == 0

    @pytest.mark.asyncio
    async def test_tree_persisted_in_db(self, orch):
        tree, root = await orch.create_tree("Persisted")
        fetched = await get_tree(tree.id)
        assert fetched is not None
        assert fetched.name == "Persisted"
        assert fetched.root_node_id == root.id

    @pytest.mark.asyncio
    async def test_custom_repo_mode(self, orch, tmp_workspaces):
        """create_tree with local clone from a source repo."""
        # Set up a source repo to clone from
        src = tmp_workspaces / "_source"
        src.mkdir()
        await _run_git(src, "init")
        await _run_git(src, "config", "user.email", "t@t")
        await _run_git(src, "config", "user.name", "T")
        (src / "README.md").write_text("# Source\n")
        await _run_git(src, "add", "-A")
        await _run_git(src, "commit", "-m", "init")

        tree, root = await orch.create_tree("Cloned", repo_mode="local", repo_source=str(src))
        root_dir = tmp_workspaces / tree.id / root.id
        assert (root_dir / "README.md").exists()


# ── branch ───────────────────────────────────────────────────────────


class TestBranch:

    @pytest.mark.asyncio
    async def test_creates_child_node(self, orch):
        tree, root = await orch.create_tree("T")
        child = await orch.branch(root.id, label="explore")
        assert child.parent_id == root.id
        assert child.tree_id == tree.id
        assert child.label == "explore"

    @pytest.mark.asyncio
    async def test_child_has_worktree(self, orch, tmp_workspaces):
        tree, root = await orch.create_tree("T")
        child = await orch.branch(root.id, label="wt")
        child_dir = tmp_workspaces / tree.id / child.id
        assert child_dir.exists()
        rc, _, _ = await _run_git(child_dir, "status", check=False)
        assert rc == 0

    @pytest.mark.asyncio
    async def test_child_has_own_branch(self, orch):
        _, root = await orch.create_tree("T")
        child = await orch.branch(root.id)
        assert child.git_branch == f"ct-{child.id}"

    @pytest.mark.asyncio
    async def test_worktree_isolated_from_root(self, orch, tmp_workspaces):
        tree, root = await orch.create_tree("T")
        child = await orch.branch(root.id)

        child_dir = tmp_workspaces / tree.id / child.id
        (child_dir / "child_only.txt").write_text("isolated\n")
        await _run_git(child_dir, "add", "-A")
        await _run_git(child_dir, "commit", "-m", "child change")

        root_dir = tmp_workspaces / tree.id / root.id
        assert not (root_dir / "child_only.txt").exists()

    @pytest.mark.asyncio
    async def test_created_by_default(self, orch):
        _, root = await orch.create_tree("T")
        child = await orch.branch(root.id)
        assert child.created_by == "human"

    @pytest.mark.asyncio
    async def test_created_by_shadow(self, orch):
        _, root = await orch.create_tree("T")
        child = await orch.branch(root.id, created_by="shadow")
        assert child.created_by == "shadow"
        # Verify it persists
        fetched = await get_node(child.id)
        assert fetched.created_by == "shadow"

    @pytest.mark.asyncio
    async def test_created_by_custom_user(self, orch):
        _, root = await orch.create_tree("T")
        child = await orch.branch(root.id, created_by="user:alice")
        assert child.created_by == "user:alice"

    @pytest.mark.asyncio
    async def test_multiple_branches_from_same_parent(self, orch):
        _, root = await orch.create_tree("T")
        c1 = await orch.branch(root.id, label="approach-a")
        c2 = await orch.branch(root.id, label="approach-b")
        c3 = await orch.branch(root.id, label="approach-c")

        assert c1.id != c2.id != c3.id
        parent = await get_node(root.id)
        assert set(parent.children_ids) == {c1.id, c2.id, c3.id}

    @pytest.mark.asyncio
    async def test_branch_from_branch(self, orch, tmp_workspaces):
        """Deep nesting: branch from a non-root node."""
        tree, root = await orch.create_tree("T")
        child = await orch.branch(root.id, label="level-1")

        # Make a change in child's worktree so it diverges
        child_dir = tmp_workspaces / tree.id / child.id
        (child_dir / "added.py").write_text("print('hello')\n")
        await _run_git(child_dir, "add", "-A")
        await _run_git(child_dir, "commit", "-m", "add file")
        _, child_sha, _ = await _run_git(child_dir, "rev-parse", "HEAD")
        await update_node(child.id, git_commit=child_sha)

        grandchild = await orch.branch(child.id, label="level-2")
        gc_dir = tmp_workspaces / tree.id / grandchild.id
        # Grandchild should inherit the file from its parent
        assert (gc_dir / "added.py").exists()


# ── set_repo ─────────────────────────────────────────────────────────


class TestSetRepo:

    @pytest.mark.asyncio
    async def test_reconfigure_to_local(self, orch, tmp_workspaces):
        """set_repo switches a tree from 'new' to 'local' clone."""
        # Create a source repo
        src = tmp_workspaces / "_source"
        src.mkdir()
        await _run_git(src, "init")
        await _run_git(src, "config", "user.email", "t@t")
        await _run_git(src, "config", "user.name", "T")
        (src / "app.py").write_text("# app\n")
        await _run_git(src, "add", "-A")
        await _run_git(src, "commit", "-m", "init")

        tree, root = await orch.create_tree("T")
        updated_tree, updated_root = await orch.set_repo(tree.id, "local", str(src))

        assert updated_tree.repo_mode == "local"
        assert updated_tree.repo_source == str(src)
        assert updated_root.git_commit is not None

        root_dir = tmp_workspaces / tree.id / root.id
        assert (root_dir / "app.py").exists()

    @pytest.mark.asyncio
    async def test_nonexistent_tree_raises(self, orch):
        with pytest.raises(ValueError, match="Tree not found"):
            await orch.set_repo("nonexistent", "new")

    @pytest.mark.asyncio
    async def test_returns_updated_objects(self, orch):
        tree, _ = await orch.create_tree("T")
        updated_tree, updated_root = await orch.set_repo(tree.id, "new")
        assert updated_tree.id == tree.id
        assert updated_root is not None


# ── prepare_chat ─────────────────────────────────────────────────────


class TestPrepareChat:

    @pytest.mark.asyncio
    async def test_returns_chat_context(self, orch):
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "Write a hello world program")

        assert isinstance(ctx, ChatContext)
        assert ctx.node_id != root.id  # new child node
        assert ctx.sdk_message == "Write a hello world program"
        assert ctx.workspace.exists()
        assert ctx.model  # resolved from defaults
        assert ctx.max_turns >= 0  # 0 = unlimited

    @pytest.mark.asyncio
    async def test_creates_child_node(self, orch):
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "Build a REST API")

        child = await get_node(ctx.node_id)
        assert child is not None
        assert child.parent_id == root.id
        assert child.user_message == "Build a REST API"
        assert child.status == "active"
        assert child.label == "Build a REST API"

    @pytest.mark.asyncio
    async def test_label_truncated_to_40(self, orch):
        _, root = await orch.create_tree("T")
        long_msg = "A" * 100
        ctx = await orch.prepare_chat(root.id, long_msg)

        child = await get_node(ctx.node_id)
        assert len(child.label) == 40

    @pytest.mark.asyncio
    async def test_child_has_worktree(self, orch, tmp_workspaces):
        tree, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "Hello")
        assert ctx.workspace.exists()
        rc, _, _ = await _run_git(ctx.workspace, "status", check=False)
        assert rc == 0

    @pytest.mark.asyncio
    async def test_after_id_passed_through(self, orch):
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "msg", after_id="some-sibling")
        assert ctx.after_id == "some-sibling"

    @pytest.mark.asyncio
    async def test_created_by_propagated(self, orch):
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "msg", created_by="shadow")
        child = await get_node(ctx.node_id)
        assert child.created_by == "shadow"

    @pytest.mark.asyncio
    async def test_nonexistent_parent_raises(self, orch):
        with pytest.raises(ValueError, match="not found"):
            await orch.prepare_chat("nonexistent", "msg")

    @pytest.mark.asyncio
    async def test_resolves_global_defaults(self, orch):
        """Settings from the DB are resolved into the ChatContext."""
        await set_setting("default_model", "claude-opus-4-6")
        await set_setting("default_max_turns", "10")

        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "msg")
        assert ctx.model == "claude-opus-4-6"
        assert ctx.max_turns == 10

    @pytest.mark.asyncio
    async def test_tree_overrides_global_defaults(self, orch):
        """Per-tree settings override global defaults."""
        from services.tree_service import update_tree
        await set_setting("default_model", "claude-sonnet-4-6")
        await set_setting("default_max_turns", "25")

        tree, root = await orch.create_tree("T")
        await update_tree(tree.id, model="claude-opus-4-6", max_turns=5)

        ctx = await orch.prepare_chat(root.id, "msg")
        assert ctx.model == "claude-opus-4-6"
        assert ctx.max_turns == 5

    @pytest.mark.asyncio
    async def test_parent_session_id_none_for_root(self, orch):
        """Root node has no parent session, so parent_session_id is None."""
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "msg")
        assert ctx.parent_session_id is None

    @pytest.mark.asyncio
    async def test_cancelled_parent_prepends_context(self, orch):
        """If parent was cancelled, sdk_message includes the partial response."""
        _, root = await orch.create_tree("T")

        # Simulate a completed-then-cancelled child
        first_ctx = await orch.prepare_chat(root.id, "first message")
        await orch.cancel_chat(
            first_ctx.node_id,
            partial_text="I was working on...",
            active_tools=["Write"],
        )

        # Now prepare_chat from the cancelled node
        second_ctx = await orch.prepare_chat(first_ctx.node_id, "continue please")
        assert "[Cancelled by user" in second_ctx.sdk_message
        assert "I was working on..." in second_ctx.sdk_message
        assert "continue please" in second_ctx.sdk_message


# ── complete_chat ────────────────────────────────────────────────────


class TestCompleteChat:

    @pytest.mark.asyncio
    async def test_saves_response_and_status(self, orch):
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "hello")

        result = await orch.complete_chat(
            ctx.node_id, "Here is my response", "hello", ctx.workspace,
        )

        assert isinstance(result, ChatResult)
        assert result.node_id == ctx.node_id
        assert result.full_response == "Here is my response"

        node = await get_node(ctx.node_id)
        assert node.assistant_response == "Here is my response"
        assert node.status == "done"

    @pytest.mark.asyncio
    async def test_auto_commits_changes(self, orch):
        """If files were changed in the workspace, complete_chat auto-commits."""
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "create a file")

        # Simulate the agent writing a file
        (ctx.workspace / "hello.py").write_text("print('hello')\n")

        result = await orch.complete_chat(
            ctx.node_id, "Created hello.py", "create a file", ctx.workspace,
        )

        assert result.git_commit is not None
        assert len(result.git_commit) == 40

        node = await get_node(ctx.node_id)
        assert node.git_commit == result.git_commit

    @pytest.mark.asyncio
    async def test_no_changes_still_succeeds(self, orch):
        """complete_chat succeeds even when no files were changed (no-op commit)."""
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "explain something")

        result = await orch.complete_chat(
            ctx.node_id, "Just an explanation", "explain something", ctx.workspace,
        )

        assert result.git_commit is not None  # still returns HEAD sha
        node = await get_node(ctx.node_id)
        assert node.status == "done"

    @pytest.mark.asyncio
    async def test_git_commit_persisted(self, orch):
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "msg")
        (ctx.workspace / "file.txt").write_text("data\n")

        result = await orch.complete_chat(ctx.node_id, "resp", "msg", ctx.workspace)

        # Verify the commit actually exists in the git repo
        rc, _, _ = await _run_git(ctx.workspace, "cat-file", "-t", result.git_commit)
        assert rc == 0


# ── cancel_chat ──────────────────────────────────────────────────────


class TestCancelChat:

    @pytest.mark.asyncio
    async def test_saves_partial_with_marker(self, orch):
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "do something big")

        result = await orch.cancel_chat(ctx.node_id, "partial output", [])

        assert isinstance(result, CancelResult)
        assert result.node_id == ctx.node_id
        assert "*[Cancelled by user]*" in result.saved_text

        node = await get_node(ctx.node_id)
        assert node.status == "error"
        assert node.assistant_response == "partial output\n\n---\n*[Cancelled by user]*"

    @pytest.mark.asyncio
    async def test_includes_active_tools(self, orch):
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "msg")

        result = await orch.cancel_chat(ctx.node_id, "partial", ["Write", "Bash"])

        assert "Write" in result.saved_text
        assert "Bash" in result.saved_text

        node = await get_node(ctx.node_id)
        assert "while running: Write, Bash" in node.assistant_response

    @pytest.mark.asyncio
    async def test_empty_partial_text(self, orch):
        """Cancel with no output yet still saves the marker."""
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "msg")

        result = await orch.cancel_chat(ctx.node_id, "", [])

        node = await get_node(ctx.node_id)
        assert node.assistant_response.startswith("\n\n---\n*[Cancelled by user]*")
        assert node.status == "error"


# ── fail_chat ────────────────────────────────────────────────────────


class TestFailChat:

    @pytest.mark.asyncio
    async def test_marks_node_as_error(self, orch):
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "msg")

        await orch.fail_chat(ctx.node_id)

        node = await get_node(ctx.node_id)
        assert node.status == "error"

    @pytest.mark.asyncio
    async def test_does_not_overwrite_response(self, orch):
        """fail_chat only sets status, does not touch assistant_response."""
        _, root = await orch.create_tree("T")
        ctx = await orch.prepare_chat(root.id, "msg")
        await update_node(ctx.node_id, assistant_response="partial data")

        await orch.fail_chat(ctx.node_id)

        node = await get_node(ctx.node_id)
        assert node.assistant_response == "partial data"
        assert node.status == "error"


# ── Full lifecycle ───────────────────────────────────────────────────


class TestFullLifecycle:
    """End-to-end scenarios exercising multiple orchestrator methods in sequence."""

    @pytest.mark.asyncio
    async def test_create_branch_chat_complete(self, orch):
        """Happy path: create tree → prepare chat → complete chat."""
        tree, root = await orch.create_tree("Full Test")

        ctx = await orch.prepare_chat(root.id, "Write hello.py")
        (ctx.workspace / "hello.py").write_text("print('hello')\n")

        result = await orch.complete_chat(
            ctx.node_id, "I created hello.py", "Write hello.py", ctx.workspace,
        )

        assert result.git_commit is not None

        # Verify final state
        node = await get_node(ctx.node_id)
        assert node.status == "done"
        assert node.user_message == "Write hello.py"
        assert node.assistant_response == "I created hello.py"
        assert node.git_commit == result.git_commit
        assert node.created_by == "human"

    @pytest.mark.asyncio
    async def test_branch_inherits_parent_files(self, orch, tmp_workspaces):
        """Child branch sees files committed in parent."""
        tree, root = await orch.create_tree("Inherit Test")

        # First chat: create a file
        ctx1 = await orch.prepare_chat(root.id, "Create app.py")
        (ctx1.workspace / "app.py").write_text("# app v1\n")
        await orch.complete_chat(
            ctx1.node_id, "Created app.py", "Create app.py", ctx1.workspace,
        )

        # Branch from the completed node
        branch_node = await orch.branch(ctx1.node_id, label="refactor")
        branch_dir = tmp_workspaces / tree.id / branch_node.id
        assert (branch_dir / "app.py").read_text() == "# app v1\n"

    @pytest.mark.asyncio
    async def test_sibling_branches_diverge(self, orch, tmp_workspaces):
        """Two branches from same parent can make independent changes."""
        tree, root = await orch.create_tree("Diverge Test")

        # Create base file
        ctx_base = await orch.prepare_chat(root.id, "Create base.py")
        (ctx_base.workspace / "base.py").write_text("# base\n")
        await orch.complete_chat(
            ctx_base.node_id, "done", "Create base.py", ctx_base.workspace,
        )

        # Branch A
        a = await orch.branch(ctx_base.node_id, label="approach-a")
        a_dir = tmp_workspaces / tree.id / a.id
        (a_dir / "feature_a.py").write_text("# feature A\n")
        await _run_git(a_dir, "add", "-A")
        await _run_git(a_dir, "commit", "-m", "add feature A")

        # Branch B
        b = await orch.branch(ctx_base.node_id, label="approach-b")
        b_dir = tmp_workspaces / tree.id / b.id
        (b_dir / "feature_b.py").write_text("# feature B\n")
        await _run_git(b_dir, "add", "-A")
        await _run_git(b_dir, "commit", "-m", "add feature B")

        # A has feature_a but not feature_b
        assert (a_dir / "feature_a.py").exists()
        assert not (a_dir / "feature_b.py").exists()
        # B has feature_b but not feature_a
        assert (b_dir / "feature_b.py").exists()
        assert not (b_dir / "feature_a.py").exists()
        # Both have the shared base
        assert (a_dir / "base.py").exists()
        assert (b_dir / "base.py").exists()

    @pytest.mark.asyncio
    async def test_cancel_then_continue(self, orch):
        """Cancel a chat, then start a new one from the cancelled node."""
        _, root = await orch.create_tree("Cancel Test")

        # First chat gets cancelled
        ctx1 = await orch.prepare_chat(root.id, "start something")
        await orch.cancel_chat(ctx1.node_id, "I was starting to...", ["Write"])

        # Continue from the cancelled node
        ctx2 = await orch.prepare_chat(ctx1.node_id, "please continue")

        assert ctx2.node_id != ctx1.node_id
        assert "[Cancelled by user" in ctx2.sdk_message
        assert "I was starting to..." in ctx2.sdk_message
        assert "please continue" in ctx2.sdk_message

        child = await get_node(ctx2.node_id)
        assert child.parent_id == ctx1.node_id

    @pytest.mark.asyncio
    async def test_shadow_agent_lifecycle(self, orch):
        """Simulate a shadow agent creating branches and completing chats."""
        _, root = await orch.create_tree("Shadow Test")

        # Shadow branches off root
        shadow_branch = await orch.branch(root.id, label="shadow-explore", created_by="shadow")
        assert shadow_branch.created_by == "shadow"

        # Shadow prepares and completes a chat
        ctx = await orch.prepare_chat(
            shadow_branch.id, "Explore the codebase", created_by="shadow",
        )
        child = await get_node(ctx.node_id)
        assert child.created_by == "shadow"

        result = await orch.complete_chat(
            ctx.node_id, "Found interesting patterns", "Explore the codebase",
            ctx.workspace,
        )
        assert result.full_response == "Found interesting patterns"

        # Human branches from shadow's work
        human_branch = await orch.branch(ctx.node_id, label="human-follow-up", created_by="human")
        assert human_branch.created_by == "human"
        assert human_branch.parent_id == ctx.node_id
