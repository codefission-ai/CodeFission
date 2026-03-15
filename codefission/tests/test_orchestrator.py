"""Integration tests for the Orchestrator — end-to-end business logic without WebSocket.

These tests exercise create_tree → branch → prepare_chat → complete_chat (and
cancel/fail variants) against a real SQLite DB and real git repos in temp dirs.
No mocks except where noted — this validates the full data flow.
"""

import pytest

from orchestrator import Orchestrator, ChatContext, ChatResult, CancelResult
from store.trees import get_node, get_tree, update_node
from store.settings import set_setting
from store.git import _run_git, _GIT_ENV


async def _init_project(project_path):
    """Initialise the project dir as a git repo with one commit.

    Returns the default branch name (may be 'main' or 'master' depending on git config).
    """
    await _run_git(project_path, "init")
    await _run_git(project_path, "config", "user.email", "test@test")
    await _run_git(project_path, "config", "user.name", "Test")
    gitignore = project_path / ".gitignore"
    gitignore.write_text(".codefission/\n.claude/\n_artifacts/\n")
    await _run_git(project_path, "add", "-A")
    await _run_git(project_path, "commit", "-m", "initial commit", env=_GIT_ENV)
    _, branch, _ = await _run_git(project_path, "rev-parse", "--abbrev-ref", "HEAD")
    return branch


@pytest.fixture
def orch(tmp_db, tmp_project):
    """Orchestrator with temp DB and temp project directory."""
    return Orchestrator()


@pytest.fixture
def project(tmp_project):
    """Alias for the project path."""
    return tmp_project


# ── create_tree ──────────────────────────────────────────────────────


class TestCreateTree:

    @pytest.mark.asyncio
    async def test_returns_tree_and_root(self, orch, project):
        branch = await _init_project(project)
        tree, root = await orch.create_tree("My Tree", base_branch=branch)
        assert tree.name == "My Tree"
        assert tree.base_branch is not None
        assert root.tree_id == tree.id
        assert root.parent_id is None
        assert root.label == "root"

    @pytest.mark.asyncio
    async def test_root_has_git_metadata(self, orch, project):
        branch = await _init_project(project)
        tree, root = await orch.create_tree("Git Tree", base_branch=branch)
        assert root.git_branch is not None
        assert root.git_commit is not None
        assert len(root.git_commit) == 40  # full SHA

    @pytest.mark.asyncio
    async def test_tree_persisted_in_db(self, orch, project):
        branch = await _init_project(project)
        tree, root = await orch.create_tree("Persisted", base_branch=branch)
        fetched = await get_tree(tree.id)
        assert fetched is not None
        assert fetched.name == "Persisted"
        assert fetched.root_node_id == root.id

    @pytest.mark.asyncio
    async def test_base_branch_stored(self, orch, project):
        """create_tree stores base_branch and base_commit."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)
        fetched = await get_tree(tree.id)
        assert fetched.base_branch is not None
        assert fetched.base_commit is not None
        assert len(fetched.base_commit) == 40


# ── branch ───────────────────────────────────────────────────────────


class TestBranch:

    @pytest.mark.asyncio
    async def test_creates_child_node(self, orch, project):
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)
        child = await orch.branch(root.id, label="explore")
        assert child.parent_id == root.id
        assert child.tree_id == tree.id
        assert child.label == "explore"

    @pytest.mark.asyncio
    async def test_child_has_own_branch(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        child = await orch.branch(root.id)
        assert child.git_branch == f"ct-{child.id}"

    @pytest.mark.asyncio
    async def test_created_by_default(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        child = await orch.branch(root.id)
        assert child.created_by == "human"

    @pytest.mark.asyncio
    async def test_created_by_shadow(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        child = await orch.branch(root.id, created_by="shadow")
        assert child.created_by == "shadow"
        fetched = await get_node(child.id)
        assert fetched.created_by == "shadow"

    @pytest.mark.asyncio
    async def test_created_by_custom_user(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        child = await orch.branch(root.id, created_by="user:alice")
        assert child.created_by == "user:alice"

    @pytest.mark.asyncio
    async def test_multiple_branches_from_same_parent(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        c1 = await orch.branch(root.id, label="approach-a")
        c2 = await orch.branch(root.id, label="approach-b")
        c3 = await orch.branch(root.id, label="approach-c")

        assert c1.id != c2.id != c3.id
        parent = await get_node(root.id)
        assert set(parent.children_ids) == {c1.id, c2.id, c3.id}


# ── prepare_chat ─────────────────────────────────────────────────────


class TestPrepareChat:

    @pytest.mark.asyncio
    async def test_returns_chat_context(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "Write a hello world program")

        assert isinstance(ctx, ChatContext)
        assert ctx.node_id != root.id  # new child node
        assert ctx.sdk_message  # contains the user message
        assert "Write a hello world program" in ctx.sdk_message
        assert ctx.workspace.exists()
        assert ctx.model  # resolved from defaults
        assert ctx.max_turns >= 0

    @pytest.mark.asyncio
    async def test_creates_child_node(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "Build a REST API")

        child = await get_node(ctx.node_id)
        assert child is not None
        assert child.parent_id == root.id
        assert child.user_message == "Build a REST API"
        assert child.status == "active"
        assert child.label == "Build a REST API"

    @pytest.mark.asyncio
    async def test_label_truncated_to_40(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        long_msg = "A" * 100
        ctx = await orch.prepare_chat(root.id, long_msg)

        child = await get_node(ctx.node_id)
        assert len(child.label) == 40

    @pytest.mark.asyncio
    async def test_child_has_worktree(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "Hello")
        assert ctx.workspace.exists()
        rc, _, _ = await _run_git(ctx.workspace, "status", check=False)
        assert rc == 0

    @pytest.mark.asyncio
    async def test_after_id_passed_through(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "msg", after_id="some-sibling")
        assert ctx.after_id == "some-sibling"

    @pytest.mark.asyncio
    async def test_created_by_propagated(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "msg", created_by="shadow")
        child = await get_node(ctx.node_id)
        assert child.created_by == "shadow"

    @pytest.mark.asyncio
    async def test_nonexistent_parent_raises(self, orch, project):
        branch = await _init_project(project)
        with pytest.raises(ValueError, match="not found"):
            await orch.prepare_chat("nonexistent", "msg")

    @pytest.mark.asyncio
    async def test_resolves_global_defaults(self, orch, project):
        """Settings from the DB are resolved into the ChatContext."""
        branch = await _init_project(project)
        await set_setting("default_model", "claude-opus-4-6")
        await set_setting("default_max_turns", "10")

        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "msg")
        assert ctx.model == "claude-opus-4-6"
        assert ctx.max_turns == 10

    @pytest.mark.asyncio
    async def test_tree_overrides_global_defaults(self, orch, project):
        """Per-tree settings override global defaults."""
        from store.trees import update_tree
        branch = await _init_project(project)
        await set_setting("default_model", "claude-sonnet-4-6")
        await set_setting("default_max_turns", "25")

        tree, root = await orch.create_tree("T", base_branch=branch)
        await update_tree(tree.id, model="claude-opus-4-6", max_turns=5)

        ctx = await orch.prepare_chat(root.id, "msg")
        assert ctx.model == "claude-opus-4-6"
        assert ctx.max_turns == 5

    @pytest.mark.asyncio
    async def test_parent_session_id_none_for_root(self, orch, project):
        """Root node has no parent session, so parent_session_id is None."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "msg")
        assert ctx.parent_session_id is None

    @pytest.mark.asyncio
    async def test_cancelled_parent_prepends_context(self, orch, project):
        """If parent was cancelled, sdk_message includes the partial response."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)

        first_ctx = await orch.prepare_chat(root.id, "first message")
        await orch.cancel_chat(
            first_ctx.node_id,
            partial_text="I was working on...",
            active_tools=["Write"],
            workspace=first_ctx.workspace,
        )

        second_ctx = await orch.prepare_chat(first_ctx.node_id, "continue please")
        assert "[Cancelled by user" in second_ctx.sdk_message
        assert "I was working on..." in second_ctx.sdk_message
        assert "continue please" in second_ctx.sdk_message


# ── complete_chat ────────────────────────────────────────────────────


class TestCompleteChat:

    @pytest.mark.asyncio
    async def test_saves_response_and_status(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
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
    async def test_auto_commits_changes(self, orch, project):
        """If files were changed in the workspace, complete_chat auto-commits."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "create a file")

        (ctx.workspace / "hello.py").write_text("print('hello')\n")

        result = await orch.complete_chat(
            ctx.node_id, "Created hello.py", "create a file", ctx.workspace,
        )

        assert result.git_commit is not None
        assert len(result.git_commit) == 40

        node = await get_node(ctx.node_id)
        assert node.git_commit == result.git_commit

    @pytest.mark.asyncio
    async def test_no_changes_still_succeeds(self, orch, project):
        """complete_chat succeeds even when no files were changed."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "explain something")

        result = await orch.complete_chat(
            ctx.node_id, "Just an explanation", "explain something", ctx.workspace,
        )

        assert result.git_commit is not None
        node = await get_node(ctx.node_id)
        assert node.status == "done"

    @pytest.mark.asyncio
    async def test_git_commit_persisted(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "msg")
        (ctx.workspace / "file.txt").write_text("data\n")

        result = await orch.complete_chat(ctx.node_id, "resp", "msg", ctx.workspace)

        rc, _, _ = await _run_git(ctx.workspace, "cat-file", "-t", result.git_commit)
        assert rc == 0


# ── cancel_chat ──────────────────────────────────────────────────────


class TestCancelChat:

    @pytest.mark.asyncio
    async def test_saves_partial_with_marker(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "do something big")

        result = await orch.cancel_chat(ctx.node_id, "partial output", [])

        assert isinstance(result, CancelResult)
        assert result.node_id == ctx.node_id
        assert "*[Cancelled by user]*" in result.saved_text

        node = await get_node(ctx.node_id)
        assert node.status == "error"
        assert node.assistant_response == "partial output\n\n---\n*[Cancelled by user]*"

    @pytest.mark.asyncio
    async def test_includes_active_tools(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "msg")

        result = await orch.cancel_chat(ctx.node_id, "partial", ["Write", "Bash"])

        assert "Write" in result.saved_text
        assert "Bash" in result.saved_text

        node = await get_node(ctx.node_id)
        assert "while running: Write, Bash" in node.assistant_response

    @pytest.mark.asyncio
    async def test_empty_partial_text(self, orch, project):
        """Cancel with no output yet still saves the marker."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "msg")

        result = await orch.cancel_chat(ctx.node_id, "", [])

        node = await get_node(ctx.node_id)
        assert node.assistant_response.startswith("\n\n---\n*[Cancelled by user]*")
        assert node.status == "error"


# ── fail_chat ────────────────────────────────────────────────────────


class TestFailChat:

    @pytest.mark.asyncio
    async def test_marks_node_as_error(self, orch, project):
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "msg")

        await orch.fail_chat(ctx.node_id)

        node = await get_node(ctx.node_id)
        assert node.status == "error"

    @pytest.mark.asyncio
    async def test_does_not_overwrite_response(self, orch, project):
        """fail_chat only sets status, does not touch assistant_response."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
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
    async def test_create_branch_chat_complete(self, orch, project):
        """Happy path: create tree → prepare chat → complete chat."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("Full Test", base_branch=branch)

        ctx = await orch.prepare_chat(root.id, "Write hello.py")
        (ctx.workspace / "hello.py").write_text("print('hello')\n")

        result = await orch.complete_chat(
            ctx.node_id, "I created hello.py", "Write hello.py", ctx.workspace,
        )

        assert result.git_commit is not None

        node = await get_node(ctx.node_id)
        assert node.status == "done"
        assert node.user_message == "Write hello.py"
        assert node.assistant_response == "I created hello.py"
        assert node.git_commit == result.git_commit
        assert node.created_by == "human"

    @pytest.mark.asyncio
    async def test_branch_inherits_parent_files(self, orch, project):
        """Child branch sees files committed in parent."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("Inherit Test", base_branch=branch)

        # First chat: create a file
        ctx1 = await orch.prepare_chat(root.id, "Create app.py")
        (ctx1.workspace / "app.py").write_text("# app v1\n")
        await orch.complete_chat(
            ctx1.node_id, "Created app.py", "Create app.py", ctx1.workspace,
        )

        # Branch from the completed node
        branch_node = await orch.branch(ctx1.node_id, label="refactor")

        # Ensure the worktree exists (branch creates lazily)
        from store.git import ensure_worktree
        wt = await ensure_worktree(
            tree.root_node_id, branch_node.id,
            ctx1.node_id, branch_node.git_commit,
        )
        assert (wt / "app.py").read_text() == "# app v1\n"

    @pytest.mark.asyncio
    async def test_sibling_branches_diverge(self, orch, project):
        """Two branches from same parent can make independent changes."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("Diverge Test", base_branch=branch)

        # Create base file
        ctx_base = await orch.prepare_chat(root.id, "Create base.py")
        (ctx_base.workspace / "base.py").write_text("# base\n")
        await orch.complete_chat(
            ctx_base.node_id, "done", "Create base.py", ctx_base.workspace,
        )

        # Branch A — prepare a chat to get workspace
        ctx_a = await orch.prepare_chat(ctx_base.node_id, "Feature A")
        (ctx_a.workspace / "feature_a.py").write_text("# feature A\n")
        await _run_git(ctx_a.workspace, "add", "-A")
        await _run_git(ctx_a.workspace, "commit", "-m", "add feature A", env=_GIT_ENV)

        # Branch B — prepare a chat to get workspace
        ctx_b = await orch.prepare_chat(ctx_base.node_id, "Feature B")
        (ctx_b.workspace / "feature_b.py").write_text("# feature B\n")
        await _run_git(ctx_b.workspace, "add", "-A")
        await _run_git(ctx_b.workspace, "commit", "-m", "add feature B", env=_GIT_ENV)

        # A has feature_a but not feature_b
        assert (ctx_a.workspace / "feature_a.py").exists()
        assert not (ctx_a.workspace / "feature_b.py").exists()
        # B has feature_b but not feature_a
        assert (ctx_b.workspace / "feature_b.py").exists()
        assert not (ctx_b.workspace / "feature_a.py").exists()
        # Both have the shared base
        assert (ctx_a.workspace / "base.py").exists()
        assert (ctx_b.workspace / "base.py").exists()

    @pytest.mark.asyncio
    async def test_cancel_then_continue(self, orch, project):
        """Cancel a chat, then start a new one from the cancelled node."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("Cancel Test", base_branch=branch)

        ctx1 = await orch.prepare_chat(root.id, "start something")
        await orch.cancel_chat(ctx1.node_id, "I was starting to...", ["Write"],
                               workspace=ctx1.workspace)

        ctx2 = await orch.prepare_chat(ctx1.node_id, "please continue")

        assert ctx2.node_id != ctx1.node_id
        assert "[Cancelled by user" in ctx2.sdk_message
        assert "I was starting to..." in ctx2.sdk_message
        assert "please continue" in ctx2.sdk_message

        child = await get_node(ctx2.node_id)
        assert child.parent_id == ctx1.node_id

    @pytest.mark.asyncio
    async def test_shadow_agent_lifecycle(self, orch, project):
        """Simulate a shadow agent creating branches and completing chats."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("Shadow Test", base_branch=branch)

        shadow_branch = await orch.branch(root.id, label="shadow-explore", created_by="shadow")
        assert shadow_branch.created_by == "shadow"

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

        human_branch = await orch.branch(ctx.node_id, label="human-follow-up", created_by="human")
        assert human_branch.created_by == "human"
        assert human_branch.parent_id == ctx.node_id
