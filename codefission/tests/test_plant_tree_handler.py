"""Handler-level test for 'Plant new tree' button.

The existing tests in test_plant_tree_bug.py and test_planted_tree_runtime_bugs.py
call orch.create_tree() directly. This file exercises the actual WS dispatch path:

    frontend button click
        → send({ type: CREATE_TREE, name, from_node_id, repo_id, repo_path })
        → ConnectionHandler.dispatch()
        → handle_create_tree(data)
        → orch.create_tree(...)
        → send(TREE_CREATED, tree=..., root=...)

If ANYTHING in that chain raises an exception, the handler swallows it and sends
WS.ERROR. The frontend WS.ERROR handler only logs to console — the user sees nothing.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

from agentbridge import SessionInit, TextDelta, TurnComplete
from orchestrator import Orchestrator
from store.trees import get_node, get_tree
from store.git import _run_git, _GIT_ENV
from events import WS
from config import set_project_path


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _init_project(project_path):
    await _run_git(project_path, "init")
    await _run_git(project_path, "config", "user.email", "test@test")
    await _run_git(project_path, "config", "user.name", "Test")
    (project_path / ".gitignore").write_text(".codefission/\n.claude/\n_artifacts/\n")
    await _run_git(project_path, "add", "-A")
    await _run_git(project_path, "commit", "-m", "initial commit", env=_GIT_ENV)
    _, branch, _ = await _run_git(project_path, "rev-parse", "--abbrev-ref", "HEAD")
    return branch.strip()


def _make_mock_stream(session_id="test-session", text="ok"):
    async def mock_stream(*args, **kwargs):
        yield SessionInit(session_id=session_id, provider="claude")
        yield TextDelta(text=text, provider="claude")
        yield TurnComplete(session_id=session_id, cost_usd=0.01, provider="claude")
    return mock_stream


async def _run_chat(orch, parent_id, write_file=None, project=None):
    """Run a mocked chat that optionally writes a file, returns the chat node."""
    node_id = None

    async def _side_effect(*args, **kwargs):
        if write_file and project:
            workspace = args[2] if len(args) > 2 else kwargs.get("workspace")
            if workspace:
                ws = Path(workspace) if not isinstance(workspace, Path) else workspace
                ws.mkdir(parents=True, exist_ok=True)
                (ws / write_file).write_text("# generated\n")
        async for ev in _make_mock_stream()(*args, **kwargs):
            yield ev

    with patch("orchestrator.chat.stream_chat", side_effect=_side_effect):
        async for event in orch.chat(parent_id, "write something"):
            if type(event).__name__ == "ChatNodeCreated":
                node_id = event.node.id

    return await get_node(node_id)


def _make_handler(repo_path, repo_id, orch):
    """Build a ConnectionHandler with a captured send() and no real WebSocket."""
    from handlers import ConnectionHandler

    # Minimal WebSocket stub — send_json must exist but never called directly
    ws_stub = MagicMock()
    ws_stub.send_json = AsyncMock()

    handler = ConnectionHandler(
        ws=ws_stub,
        repo_path=str(repo_path),
        repo_id=repo_id,
        orchestrator=orch,
    )

    # Capture sent messages
    sent = []

    async def capture_send(msg_type, **payload):
        sent.append({"type": msg_type, **payload})

    handler.send = capture_send
    return handler, sent


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def orch(tmp_db, tmp_project):
    return Orchestrator()


@pytest.fixture
def project(tmp_project):
    return tmp_project


# ── Tests ────────────────────────────────────────────────────────────────────


class TestPlantTreeButtonDispatch:
    """The button sends CREATE_TREE via WS; test the full dispatch path."""

    @pytest.mark.asyncio
    async def test_handler_returns_tree_created_not_error(self, orch, project):
        """handle_create_tree must send TREE_CREATED, not WS.ERROR.

        If WS.ERROR is sent, the frontend silently swallows it and the user
        sees 'nothing happened'.
        """
        git_branch = await _init_project(project)
        _, first_sha, _ = await _run_git(project, "rev-list", "--max-parents=0", "HEAD")
        repo_id = first_sha.strip()

        source_tree, root = await orch.create_tree(
            "Source", base_branch=git_branch,
            repo_id=repo_id, repo_path=str(project), repo_name="proj",
        )

        chat_node = await _run_chat(orch, root.id, write_file="hello.py", project=project)
        assert chat_node.git_commit is not None, "Precondition: chat node needs a commit"

        handler, sent = _make_handler(project, repo_id, orch)

        # Exactly the payload the frontend button sends
        await handler.dispatch({
            "type": WS.CREATE_TREE,
            "name": "My new tree",
            "from_node_id": chat_node.id,
            "repo_id": source_tree.repo_id,
            "repo_path": source_tree.repo_path,
        })

        types = [m["type"] for m in sent]
        assert WS.ERROR not in types, (
            f"handle_create_tree sent WS.ERROR instead of TREE_CREATED.\n"
            f"Error message: {next((m.get('error') for m in sent if m['type'] == WS.ERROR), 'unknown')}\n"
            f"Full sent messages: {sent}"
        )
        assert WS.TREE_CREATED in types, (
            f"Expected TREE_CREATED but got: {types}"
        )

    @pytest.mark.asyncio
    async def test_handler_tree_created_has_correct_base_commit(self, orch, project):
        """The TREE_CREATED response must have base_commit == source node's git_commit."""
        git_branch = await _init_project(project)
        _, first_sha, _ = await _run_git(project, "rev-list", "--max-parents=0", "HEAD")
        repo_id = first_sha.strip()

        source_tree, root = await orch.create_tree(
            "Source", base_branch=git_branch,
            repo_id=repo_id, repo_path=str(project), repo_name="proj",
        )

        chat_node = await _run_chat(orch, root.id, write_file="hello.py", project=project)

        handler, sent = _make_handler(project, repo_id, orch)

        await handler.dispatch({
            "type": WS.CREATE_TREE,
            "name": "My new tree",
            "from_node_id": chat_node.id,
            "repo_id": source_tree.repo_id,
            "repo_path": source_tree.repo_path,
        })

        created = next((m for m in sent if m["type"] == WS.TREE_CREATED), None)
        assert created is not None, f"No TREE_CREATED message. Got: {[m['type'] for m in sent]}"

        tree_data = created["tree"]
        root_data = created["root"]

        assert tree_data["base_commit"] == chat_node.git_commit, (
            f"New tree base_commit should be chat node's commit {chat_node.git_commit[:7]}, "
            f"got {tree_data['base_commit'][:7] if tree_data['base_commit'] else 'None'}"
        )
        assert root_data["git_commit"] == chat_node.git_commit, (
            f"New root's git_commit should be {chat_node.git_commit[:7]}, "
            f"got {root_data['git_commit'][:7] if root_data['git_commit'] else 'None'}"
        )

    @pytest.mark.asyncio
    async def test_handler_tree_created_has_correct_base_branch(self, orch, project):
        """The TREE_CREATED response must have base_branch == source tree's branch, not ct-."""
        git_branch = await _init_project(project)
        _, first_sha, _ = await _run_git(project, "rev-list", "--max-parents=0", "HEAD")
        repo_id = first_sha.strip()

        source_tree, root = await orch.create_tree(
            "Source", base_branch=git_branch,
            repo_id=repo_id, repo_path=str(project), repo_name="proj",
        )

        chat_node = await _run_chat(orch, root.id, write_file="hello.py", project=project)

        handler, sent = _make_handler(project, repo_id, orch)

        await handler.dispatch({
            "type": WS.CREATE_TREE,
            "name": "My new tree",
            "from_node_id": chat_node.id,
            "repo_id": source_tree.repo_id,
            "repo_path": source_tree.repo_path,
        })

        created = next((m for m in sent if m["type"] == WS.TREE_CREATED), None)
        assert created is not None

        tree_data = created["tree"]
        assert not tree_data["base_branch"].startswith("ct-"), (
            f"base_branch should be '{git_branch}', not internal ct- name: {tree_data['base_branch']}"
        )
        assert tree_data["base_branch"] == git_branch, (
            f"Expected base_branch='{git_branch}', got '{tree_data['base_branch']}'"
        )

    @pytest.mark.asyncio
    async def test_handler_root_files_include_chat_node_file(self, orch, project):
        """After planting, list_node_files on the new root must include hello.py."""
        git_branch = await _init_project(project)
        _, first_sha, _ = await _run_git(project, "rev-list", "--max-parents=0", "HEAD")
        repo_id = first_sha.strip()

        source_tree, root = await orch.create_tree(
            "Source", base_branch=git_branch,
            repo_id=repo_id, repo_path=str(project), repo_name="proj",
        )

        chat_node = await _run_chat(orch, root.id, write_file="hello.py", project=project)

        handler, sent = _make_handler(project, repo_id, orch)

        await handler.dispatch({
            "type": WS.CREATE_TREE,
            "name": "My new tree",
            "from_node_id": chat_node.id,
            "repo_id": source_tree.repo_id,
            "repo_path": source_tree.repo_path,
        })

        created = next((m for m in sent if m["type"] == WS.TREE_CREATED), None)
        assert created is not None, f"No TREE_CREATED. Got: {[m['type'] for m in sent]}"

        new_root_id = created["root"]["id"]
        files = await orch.list_node_files(new_root_id)
        assert "hello.py" in files.files, (
            f"New tree root should have hello.py (from planted commit), got: {files.files}"
        )

    @pytest.mark.asyncio
    async def test_handler_git_graph_path_no_from_node(self, orch, project):
        """Git graph sends base_commit (no from_node_id). Handler must resolve it correctly."""
        git_branch = await _init_project(project)
        _, first_sha, _ = await _run_git(project, "rev-list", "--max-parents=0", "HEAD")
        repo_id = first_sha.strip()

        # Get initial commit SHA (what the git graph would send)
        _, initial_sha, _ = await _run_git(project, "rev-parse", "HEAD")
        initial_sha = initial_sha.strip()

        # Advance HEAD so it's not the same as initial_sha
        (project / "v2.py").write_text("x = 2\n")
        await _run_git(project, "add", "v2.py")
        await _run_git(project, "commit", "-m", "second commit", env=_GIT_ENV)

        handler, sent = _make_handler(project, repo_id, orch)

        # Exactly what the git graph sends
        await handler.dispatch({
            "type": WS.CREATE_TREE,
            "name": "Untitled",
            "base_branch": git_branch,
            "base_commit": initial_sha,
            "repo_id": repo_id,
            "repo_path": str(project),
        })

        types = [m["type"] for m in sent]
        assert WS.ERROR not in types, (
            f"Git graph path sent WS.ERROR: "
            f"{next((m.get('error') for m in sent if m['type'] == WS.ERROR), 'unknown')}"
        )
        assert WS.TREE_CREATED in types

        created = next(m for m in sent if m["type"] == WS.TREE_CREATED)
        assert created["tree"]["base_commit"] == initial_sha, (
            f"Should plant at selected commit {initial_sha[:7]}, "
            f"got {created['tree']['base_commit'][:7]}"
        )
