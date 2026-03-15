"""Tests for Phase 1 backend rewrite — new functionality.

Tests cover:
- provider/model columns on nodes table
- get_ancestor_chain() in tree_service
- Domain events and result types in orchestrator
- resolve_session_continuity() in chat_service
- Orchestrator.delete_node()
- Orchestrator.update_base()
- Orchestrator.update_global_settings() / update_tree_settings()
- Orchestrator file operations (list_node_files, get_node_diff, read_node_file)
- Sandbox removal (sandbox module no longer importable)
"""

import json
import pytest

from db import get_db
from models import Node
from orchestrator import (
    Orchestrator, ChatContext, ChatResult, CancelResult,
    ChatNodeCreated, ChatCompleted,
    DeleteNodeResult, UpdateBaseResult,
    FileListResult, DiffResult, FileContentResult,
    StreamState,
)
from store.trees import (
    get_node, get_tree, update_node,
    get_ancestor_chain, get_path_to_root,
    update_tree, create_child_node,
)
from store.settings import set_setting, get_setting
from store.git import _run_git, _GIT_ENV


async def _init_project(project_path):
    """Initialise the project dir as a git repo with one commit."""
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
    return Orchestrator()


@pytest.fixture
def project(tmp_project):
    return tmp_project


# ── DB migration: provider/model on nodes ────────────────────────────


class TestNodeProviderModelColumns:

    @pytest.mark.asyncio
    async def test_nodes_have_provider_model_columns(self, tmp_db):
        """Migration adds provider and model columns to nodes table."""
        async with get_db() as db:
            cursor = await db.execute("PRAGMA table_info(nodes)")
            cols = {row[1] for row in await cursor.fetchall()}
            assert "provider" in cols
            assert "model" in cols

    @pytest.mark.asyncio
    async def test_update_node_saves_provider_model(self, orch, project):
        """update_node can save provider and model fields."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "hello")

        await update_node(ctx.node_id, provider="claude", model="claude-opus-4-6")
        node = await get_node(ctx.node_id)
        assert node.provider == "claude"
        assert node.model == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_node_model_dump_includes_provider_model(self, orch, project):
        """Node.model_dump() includes provider and model fields."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "hi")
        await update_node(ctx.node_id, provider="codex", model="o4-mini")

        node = await get_node(ctx.node_id)
        dump = node.model_dump()
        assert dump["provider"] == "codex"
        assert dump["model"] == "o4-mini"

    @pytest.mark.asyncio
    async def test_root_node_has_null_provider_model(self, orch, project):
        """Root nodes have null provider and model."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        node = await get_node(root.id)
        assert node.provider is None
        assert node.model is None

    @pytest.mark.asyncio
    async def test_complete_chat_saves_provider_model(self, orch, project):
        """complete_chat can save provider and model on the node."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "make a file")
        (ctx.workspace / "hello.py").write_text("print('hi')\n")

        result = await orch.complete_chat(
            ctx.node_id, "done", "make a file", ctx.workspace,
            provider="claude", model="claude-opus-4-6",
        )

        node = await get_node(ctx.node_id)
        assert node.provider == "claude"
        assert node.model == "claude-opus-4-6"
        assert node.status == "done"


# ── get_ancestor_chain ───────────────────────────────────────────────


class TestGetAncestorChain:

    @pytest.mark.asyncio
    async def test_root_has_no_ancestors(self, orch, project):
        """Root node returns empty ancestor chain."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        chain = await get_ancestor_chain(root.id)
        assert chain == []

    @pytest.mark.asyncio
    async def test_child_ancestors_include_root(self, orch, project):
        """Child node's ancestor chain includes root."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        child = await orch.branch(root.id, "child")
        chain = await get_ancestor_chain(child.id)
        assert len(chain) == 1
        assert chain[0].id == root.id

    @pytest.mark.asyncio
    async def test_grandchild_ancestor_chain(self, orch, project):
        """Grandchild's ancestor chain is [root, parent]."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx1 = await orch.prepare_chat(root.id, "first")
        await orch.complete_chat(ctx1.node_id, "done", "first", ctx1.workspace)

        ctx2 = await orch.prepare_chat(ctx1.node_id, "second")
        chain = await get_ancestor_chain(ctx2.node_id)
        assert len(chain) == 2
        assert chain[0].id == root.id
        assert chain[1].id == ctx1.node_id

    @pytest.mark.asyncio
    async def test_ancestor_chain_excludes_node_itself(self, orch, project):
        """get_ancestor_chain excludes the queried node."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        child = await orch.branch(root.id, "child")
        chain = await get_ancestor_chain(child.id)
        node_ids = [n.id for n in chain]
        assert child.id not in node_ids

    @pytest.mark.asyncio
    async def test_nonexistent_node_returns_empty(self, orch, project):
        """Nonexistent node returns empty chain."""
        branch = await _init_project(project)
        chain = await get_ancestor_chain("nonexistent")
        assert chain == []


# ── Domain events ────────────────────────────────────────────────────


class TestDomainEvents:

    def test_chat_node_created(self):
        """ChatNodeCreated holds a node and optional after_id."""
        node = Node(id="n1", tree_id="t1")
        event = ChatNodeCreated(node=node, after_id="n0")
        assert event.node.id == "n1"
        assert event.after_id == "n0"

    def test_chat_completed(self):
        """ChatCompleted wraps a ChatResult."""
        result = ChatResult(node_id="n1", full_response="done")
        event = ChatCompleted(result=result)
        assert event.result.node_id == "n1"
        assert event.result.full_response == "done"

    def test_stream_state(self):
        """StreamState tracks streaming state."""
        state = StreamState(node_id="n1", tree_id="t1")
        assert state.status == "active"
        assert state.cancelled is False
        assert state.text == ""


# ── resolve_session_continuity ────────────────────────────────────────


class TestResolveSessionContinuity:

    @pytest.mark.asyncio
    async def test_root_node_returns_fresh_start(self, orch, project):
        """Root node with no user_message returns fresh start."""
        from store.ai import resolve_session_continuity
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        node = await get_node(root.id)

        resume_id, fork, prior = await resolve_session_continuity(node, "claude")
        assert resume_id is None
        assert fork is False
        assert prior is None

    @pytest.mark.asyncio
    async def test_same_provider_forks_session(self, orch, project):
        """Same provider with session_id returns fork parameters."""
        from store.ai import resolve_session_continuity
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "hello")
        await update_node(ctx.node_id, session_id="sess_abc", provider="claude")
        node = await get_node(ctx.node_id)

        resume_id, fork, prior = await resolve_session_continuity(node, "claude")
        assert resume_id == "sess_abc"
        assert fork is True
        assert prior is None

    @pytest.mark.asyncio
    async def test_different_provider_context_transfer(self, orch, project):
        """Different provider returns context transfer text."""
        from store.ai import resolve_session_continuity
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "hello")
        await orch.complete_chat(ctx.node_id, "response", "hello", ctx.workspace,
                                 provider="claude", model="claude-opus-4-6")
        await update_node(ctx.node_id, session_id="sess_abc")
        node = await get_node(ctx.node_id)

        resume_id, fork, prior = await resolve_session_continuity(node, "codex")
        assert resume_id is None
        assert fork is False
        assert prior is not None
        assert "Previous conversation history" in prior

    @pytest.mark.asyncio
    async def test_no_session_id_context_transfer(self, orch, project):
        """No session_id on parent triggers context transfer even for same provider."""
        from store.ai import resolve_session_continuity
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "hello")
        await orch.complete_chat(ctx.node_id, "response", "hello", ctx.workspace,
                                 provider="claude", model="claude-opus-4-6")
        node = await get_node(ctx.node_id)
        # No session_id set

        resume_id, fork, prior = await resolve_session_continuity(node, "claude")
        assert resume_id is None
        assert fork is False
        assert prior is not None


# ── Orchestrator.delete_node ─────────────────────────────────────────


class TestDeleteNode:

    @pytest.mark.asyncio
    async def test_deletes_subtree(self, orch, project):
        """delete_node removes the node and its children."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        child = await orch.branch(root.id, "child")
        grandchild = await orch.branch(child.id, "grandchild")

        result = await orch.delete_node(child.id)
        assert isinstance(result, DeleteNodeResult)
        assert child.id in result.deleted_ids
        assert grandchild.id in result.deleted_ids

        # Nodes should be gone
        assert await get_node(child.id) is None
        assert await get_node(grandchild.id) is None

    @pytest.mark.asyncio
    async def test_cannot_delete_root(self, orch, project):
        """Cannot delete root node."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        with pytest.raises(ValueError, match="Cannot delete root"):
            await orch.delete_node(root.id)

    @pytest.mark.asyncio
    async def test_nonexistent_node_raises(self, orch, project):
        """Nonexistent node raises ValueError."""
        branch = await _init_project(project)
        with pytest.raises(ValueError, match="not found"):
            await orch.delete_node("nonexistent")

    @pytest.mark.asyncio
    async def test_cleans_up_settings(self, orch, project):
        """delete_node cleans expanded_nodes and collapsed_subtrees."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        child = await orch.branch(root.id, "child")

        # Set some settings referencing the child
        await set_setting("expanded_nodes", json.dumps({child.id: True, "other": True}))
        await set_setting("collapsed_subtrees", json.dumps({child.id: True}))

        await orch.delete_node(child.id)

        raw_exp = await get_setting("expanded_nodes")
        exp = json.loads(raw_exp)
        assert child.id not in exp
        assert "other" in exp

        raw_cs = await get_setting("collapsed_subtrees")
        cs = json.loads(raw_cs)
        assert child.id not in cs


# ── Orchestrator.update_base ─────────────────────────────────────────


class TestUpdateBase:

    @pytest.mark.asyncio
    async def test_updates_base_commit(self, orch, project):
        """update_base changes the tree's base_commit."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)

        # Make a second commit
        (project / "new.txt").write_text("data\n")
        await _run_git(project, "add", "-A")
        await _run_git(project, "commit", "-m", "second", env=_GIT_ENV)
        _, new_sha, _ = await _run_git(project, "rev-parse", "HEAD")

        result = await orch.update_base(tree.id, new_commit=new_sha)
        assert isinstance(result, UpdateBaseResult)
        assert result.tree.base_commit == new_sha.strip()
        assert result.existing_tree_id is None

    @pytest.mark.asyncio
    async def test_cannot_update_base_with_children(self, orch, project):
        """Cannot update base after conversations started."""
        branch = await _init_project(project)
        tree, root = await orch.create_tree("T", base_branch=branch)
        await orch.branch(root.id, "child")

        with pytest.raises(ValueError, match="Cannot change base"):
            await orch.update_base(tree.id, new_branch="main")

    @pytest.mark.asyncio
    async def test_nonexistent_tree_raises(self, orch, project):
        """Nonexistent tree raises ValueError."""
        branch = await _init_project(project)
        with pytest.raises(ValueError, match="not found"):
            await orch.update_base("nonexistent")


# ── Orchestrator.update_global_settings ──────────────────────────────


class TestUpdateGlobalSettings:

    @pytest.mark.asyncio
    async def test_updates_settings(self, orch, project):
        """update_global_settings writes to settings table."""
        branch = await _init_project(project)
        defaults = await orch.update_global_settings({"default_model": "claude-sonnet-4-6"})
        assert defaults["model"] == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_clears_setting_with_empty(self, orch, project):
        """Empty string clears a setting."""
        branch = await _init_project(project)
        await orch.update_global_settings({"default_model": "claude-opus-4-6"})
        defaults = await orch.update_global_settings({"default_model": ""})
        # Should fall back to provider default
        assert defaults["model"]  # Not empty — falls back to default

    @pytest.mark.asyncio
    async def test_returns_full_defaults(self, orch, project):
        """Returns complete defaults dict."""
        branch = await _init_project(project)
        defaults = await orch.update_global_settings({})
        assert "provider" in defaults
        assert "model" in defaults


# ── Orchestrator.update_tree_settings ────────────────────────────────


class TestUpdateTreeSettings:

    @pytest.mark.asyncio
    async def test_updates_tree_model(self, orch, project):
        """update_tree_settings changes tree's model."""
        branch = await _init_project(project)
        tree, _ = await orch.create_tree("T", base_branch=branch)
        updated = await orch.update_tree_settings(tree.id, {"model": "claude-opus-4-6"})
        assert updated.model == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_updates_tree_skill(self, orch, project):
        """update_tree_settings changes tree's skill."""
        branch = await _init_project(project)
        tree, _ = await orch.create_tree("T", base_branch=branch)
        updated = await orch.update_tree_settings(tree.id, {"skill": "You are an expert."})
        assert updated.skill == "You are an expert."

    @pytest.mark.asyncio
    async def test_clears_provider(self, orch, project):
        """Empty provider clears to inherit global."""
        branch = await _init_project(project)
        tree, _ = await orch.create_tree("T", base_branch=branch)
        await orch.update_tree_settings(tree.id, {"provider": "codex"})
        updated = await orch.update_tree_settings(tree.id, {"provider": ""})
        assert updated.provider == ""


# ── Orchestrator file operations ─────────────────────────────────────


class TestFileOperations:

    @pytest.mark.asyncio
    async def test_list_node_files(self, orch, project):
        """list_node_files returns files in workspace."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "create files")
        (ctx.workspace / "hello.py").write_text("print('hi')\n")
        await orch.complete_chat(ctx.node_id, "done", "create files", ctx.workspace)

        result = await orch.list_node_files(ctx.node_id)
        assert isinstance(result, FileListResult)
        assert "hello.py" in result.files

    @pytest.mark.asyncio
    async def test_get_node_diff(self, orch, project):
        """get_node_diff returns diff for a node."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "create file")
        (ctx.workspace / "new.py").write_text("x = 1\n")
        await orch.complete_chat(ctx.node_id, "done", "create file", ctx.workspace)

        result = await orch.get_node_diff(ctx.node_id)
        assert isinstance(result, DiffResult)
        assert "new.py" in result.diff

    @pytest.mark.asyncio
    async def test_read_node_file(self, orch, project):
        """read_node_file returns file content."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        ctx = await orch.prepare_chat(root.id, "create file")
        (ctx.workspace / "data.txt").write_text("hello world\n")
        await orch.complete_chat(ctx.node_id, "done", "create file", ctx.workspace)

        result = await orch.read_node_file(ctx.node_id, "data.txt")
        assert isinstance(result, FileContentResult)
        assert "hello world" in result.content

    @pytest.mark.asyncio
    async def test_list_node_files_nonexistent(self, orch, project):
        """list_node_files raises for nonexistent node."""
        branch = await _init_project(project)
        with pytest.raises(ValueError, match="not found"):
            await orch.list_node_files("nonexistent")

    @pytest.mark.asyncio
    async def test_read_node_file_from_commit(self, orch, project):
        """read_node_file falls back to git commit when worktree is gone."""
        branch = await _init_project(project)
        _, root = await orch.create_tree("T", base_branch=branch)
        # Root node has git_commit — read .gitignore from it
        node = await get_node(root.id)
        assert node.git_commit is not None

        result = await orch.read_node_file(root.id, ".gitignore")
        assert ".codefission/" in result.content


# ── Sandbox removal ──────────────────────────────────────────────────


class TestSandboxRemoval:

    def test_sandbox_module_not_importable(self):
        """sandbox.py has been deleted and cannot be imported."""
        with pytest.raises(ModuleNotFoundError):
            import store.sandbox  # noqa: F401

    @pytest.mark.asyncio
    async def test_global_defaults_no_sandbox_keys(self, orch, project):
        """Global defaults no longer include sandbox-related keys."""
        branch = await _init_project(project)
        from store.settings import get_global_defaults
        defaults = await get_global_defaults()
        assert "sandbox" not in defaults
        assert "sandbox_available" not in defaults

    def test_chat_context_no_sandbox(self):
        """ChatContext no longer has a sandbox field."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ChatContext)}
        assert "sandbox" not in field_names
