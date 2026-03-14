"""E2E — Test full CLI-to-server workflows.

End-to-end tests that start a real FastAPI server (in-process via AsyncClient),
run CLI-equivalent operations via the REST API, and verify the full pipeline:
  - Tree creation -> listing -> deletion
  - Chat streaming with mocked orchestrator
  - Branching and provider switching
  - Settings lifecycle
  - Node deletion
  - Note lifecycle
  - Cross-view sync

The only mock is the AI provider (orchestrator.chat returns canned events).
Everything else is real: DB, git repos, orchestrator, REST routes.
"""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from services.workspace import _run_git, _GIT_ENV


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


async def _get_repo_id(project_path):
    """Get the repo id (initial commit hash) for the test project."""
    _, repo_id, _ = await _run_git(project_path, "rev-list", "--max-parents=0", "HEAD")
    return repo_id.strip()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def server(tmp_db, tmp_project):
    """In-process FastAPI server via AsyncClient.

    Returns (client, project_path, branch, repo_id). The DB and git repo are real.
    """
    project = tmp_project
    branch = await _init_project(project)
    repo_id = await _get_repo_id(project)

    os.environ["CODEFISSION_REPO_PATH"] = str(project)

    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, project, branch, repo_id

    os.environ.pop("CODEFISSION_REPO_PATH", None)


async def _create_tree(client, branch, project, repo_id, name="E2E Test"):
    """Helper to create a tree and return (tree_id, root_id)."""
    resp = await client.post("/api/trees", json={
        "name": name,
        "base_branch": branch,
        "repo_path": str(project),
        "repo_id": repo_id,
    })
    assert resp.status_code in (200, 201), f"create_tree failed: {resp.text}"
    data = resp.json()
    tree_id = data["tree"]["id"]
    root_id = data["root"]["id"]
    return tree_id, root_id


async def _chat(client, tree_id, node_id, message):
    """Send a chat message with mocked orchestrator.chat. Returns response."""
    from services.orchestrator import Orchestrator, ChatNodeCreated, ChatCompleted, ChatResult
    from services.chat import TextDelta, SessionInit

    async def mock_chat(self_orch, nid, msg, **kwargs):
        from services.trees import create_child_node, update_node
        child = await create_child_node(nid, "")
        await update_node(child.id, user_message=msg, status="active")
        # Re-fetch so the yielded node has user_message set
        from services.trees import get_node as _get
        child = await _get(child.id)
        yield ChatNodeCreated(node=child)
        yield SessionInit(session_id="test-sess")
        yield TextDelta(text=f"Response to: {msg}")
        await update_node(child.id, status="done", assistant_response=f"Response to: {msg}")
        yield ChatCompleted(result=ChatResult(
            node_id=child.id,
            full_response=f"Response to: {msg}",
            git_commit=None,
            files_changed=0,
        ))

    with patch.object(Orchestrator, "chat", mock_chat):
        resp = await client.post(
            f"/api/trees/{tree_id}/nodes/{node_id}/chat",
            json={"message": message},
        )
    assert resp.status_code == 200, f"chat failed: {resp.text}"
    return resp


async def _branch(client, tree_id, parent_id, label="branch"):
    """Create a branch and return the new node_id."""
    resp = await client.post(
        f"/api/trees/{tree_id}/nodes/{parent_id}/branch",
        json={"label": label},
    )
    assert resp.status_code in (200, 201), f"branch failed: {resp.text}"
    return resp.json()["node"]["id"]


async def _get_node_children(client, tree_id, node_id):
    """Get a node's children_ids from the API."""
    resp = await client.get(f"/api/trees/{tree_id}/nodes/{node_id}")
    assert resp.status_code == 200
    return resp.json()["node"].get("children_ids", [])


# ---------------------------------------------------------------------------
# TestE2EBasicWorkflow
# ---------------------------------------------------------------------------

class TestE2EBasicWorkflow:

    @pytest.mark.asyncio
    async def test_init_to_chat_to_log(self, server):
        """Full lifecycle: create tree -> list -> chat -> view log."""
        client, project, branch, repo_id = server

        # 1. Create tree
        tree_id, root_id = await _create_tree(client, branch, project, repo_id, "E2E test")

        # 2. List trees - should show our tree
        resp = await client.get("/api/trees")
        assert resp.status_code == 200
        trees = resp.json()["trees"]
        names = [t["name"] for t in trees]
        assert "E2E test" in names

        # 3. Chat
        await _chat(client, tree_id, root_id, "hello")

        # 4. Get root node — check it has children
        children = await _get_node_children(client, tree_id, root_id)
        assert len(children) >= 1

        child_id = children[0]

        # 5. Get child node details
        resp = await client.get(f"/api/trees/{tree_id}/nodes/{child_id}")
        assert resp.status_code == 200
        child_data = resp.json()["node"]
        assert child_data["user_message"] == "hello"
        assert child_data["status"] == "done"

        # 6. View log - should have actions
        resp = await client.get(f"/api/trees/{tree_id}/log")
        assert resp.status_code == 200
        actions = resp.json()["actions"]
        assert isinstance(actions, list)


# ---------------------------------------------------------------------------
# TestE2EBranching
# ---------------------------------------------------------------------------

class TestE2EBranching:

    @pytest.mark.asyncio
    async def test_branch_and_diverge(self, server):
        """Create a tree, chat, branch, chat on branch - nodes diverge."""
        client, project, branch, repo_id = server

        tree_id, root_id = await _create_tree(client, branch, project, repo_id, "Branch test")

        # Chat on root
        await _chat(client, tree_id, root_id, "create app.py")

        # Get the child node
        children = await _get_node_children(client, tree_id, root_id)
        child_id = children[0]

        # Branch from child
        branch_id = await _branch(client, tree_id, child_id, "try alt")

        # Chat on the branch
        await _chat(client, tree_id, branch_id, "create alt.py")

        # Get branch node's children
        branch_children = await _get_node_children(client, tree_id, branch_id)
        assert len(branch_children) >= 1

        branch_child_id = branch_children[0]

        # Verify the branch child exists and has the right message
        resp = await client.get(f"/api/trees/{tree_id}/nodes/{branch_child_id}")
        assert resp.status_code == 200
        assert resp.json()["node"]["user_message"] == "create alt.py"


# ---------------------------------------------------------------------------
# TestE2EProviderSwitch
# ---------------------------------------------------------------------------

class TestE2EProviderSwitch:

    @pytest.mark.asyncio
    async def test_switch_provider_mid_tree(self, server):
        """Switch provider via tree settings and verify it persists."""
        client, project, branch, repo_id = server

        tree_id, root_id = await _create_tree(client, branch, project, repo_id, "Provider test")

        # Set tree provider to codex
        resp = await client.patch(f"/api/trees/{tree_id}", json={"provider": "codex"})
        assert resp.status_code == 200
        assert resp.json()["tree"]["provider"] == "codex"

        # Switch to claude
        resp = await client.patch(f"/api/trees/{tree_id}", json={"provider": "claude"})
        assert resp.status_code == 200
        assert resp.json()["tree"]["provider"] == "claude"

        # Verify via GET /api/trees
        resp = await client.get("/api/trees")
        trees = resp.json()["trees"]
        tree = [t for t in trees if t["id"] == tree_id][0]
        assert tree["provider"] == "claude"


# ---------------------------------------------------------------------------
# TestE2ESettings
# ---------------------------------------------------------------------------

class TestE2ESettings:

    @pytest.mark.asyncio
    async def test_settings_lifecycle(self, server):
        """Get defaults -> update global -> update tree -> reset tree."""
        client, project, branch, repo_id = server

        # 1. Get initial settings
        resp = await client.get("/api/settings")
        assert resp.status_code == 200
        initial = resp.json()
        assert "global_defaults" in initial
        assert "providers" in initial

        # 2. Update global provider
        resp = await client.patch("/api/settings", json={
            "default_provider": "codex",
        })
        assert resp.status_code == 200

        # 3. Verify update
        resp = await client.get("/api/settings")
        settings = resp.json()
        provider = settings["global_defaults"].get("provider")
        assert provider == "codex"

        # 4. Create tree and set tree-level override
        tree_id, root_id = await _create_tree(client, branch, project, repo_id, "Settings tree")
        resp = await client.patch(f"/api/trees/{tree_id}", json={
            "model": "o4-mini",
        })
        assert resp.status_code == 200

        # 5. Verify tree-level override
        resp = await client.get("/api/trees")
        trees = resp.json()["trees"]
        tree = [t for t in trees if t["id"] == tree_id][0]
        assert tree.get("model") == "o4-mini"

        # 6. Reset tree model by setting empty
        resp = await client.patch(f"/api/trees/{tree_id}", json={
            "model": "",
        })
        assert resp.status_code == 200

        # 7. Verify reset
        resp = await client.get("/api/trees")
        trees = resp.json()["trees"]
        tree = [t for t in trees if t["id"] == tree_id][0]
        assert tree.get("model") == "" or tree.get("model") is None


# ---------------------------------------------------------------------------
# TestE2EDeleteAndCancel
# ---------------------------------------------------------------------------

class TestE2EDeleteAndCancel:

    @pytest.mark.asyncio
    async def test_delete_node(self, server):
        """Create tree, chat, branch, delete the branch node."""
        client, project, branch, repo_id = server

        tree_id, root_id = await _create_tree(client, branch, project, repo_id, "Delete test")

        # Chat to create a child
        await _chat(client, tree_id, root_id, "hello")
        children = await _get_node_children(client, tree_id, root_id)
        child_id = children[0]

        # Branch from child
        branch_id = await _branch(client, tree_id, child_id, "to delete")

        # Delete the branch
        resp = await client.delete(f"/api/trees/{tree_id}/nodes/{branch_id}")
        assert resp.status_code == 200

        # Verify branch is gone
        resp = await client.get(f"/api/trees/{tree_id}/nodes/{branch_id}")
        assert resp.status_code == 404

        # Verify parent's children no longer includes branch
        children_after = await _get_node_children(client, tree_id, child_id)
        assert branch_id not in children_after


# ---------------------------------------------------------------------------
# TestE2ENotes
# ---------------------------------------------------------------------------

class TestE2ENotes:

    @pytest.mark.asyncio
    async def test_note_lifecycle(self, server):
        """Create, list, edit, delete notes on a tree."""
        client, project, branch, repo_id = server

        tree_id, root_id = await _create_tree(client, branch, project, repo_id, "Notes test")

        # Add a note (via tree settings update with notes field)
        note = {"id": "note-1", "text": "Remember this", "x": 0, "y": 0,
                "width": 200, "height": 100}
        resp = await client.patch(f"/api/trees/{tree_id}", json={
            "notes": json.dumps([note]),
        })
        assert resp.status_code == 200

        # Verify note is stored
        resp = await client.get("/api/trees")
        trees = resp.json()["trees"]
        tree = [t for t in trees if t["id"] == tree_id][0]
        notes_raw = tree.get("notes", "[]")
        if isinstance(notes_raw, str):
            notes = json.loads(notes_raw)
        else:
            notes = notes_raw
        assert len(notes) >= 1
        assert notes[0]["text"] == "Remember this"

        # Edit the note
        note["text"] = "Updated"
        resp = await client.patch(f"/api/trees/{tree_id}", json={
            "notes": json.dumps([note]),
        })
        assert resp.status_code == 200

        # Verify edit
        resp = await client.get("/api/trees")
        trees = resp.json()["trees"]
        tree = [t for t in trees if t["id"] == tree_id][0]
        notes_raw = tree.get("notes", "[]")
        if isinstance(notes_raw, str):
            notes = json.loads(notes_raw)
        else:
            notes = notes_raw
        assert notes[0]["text"] == "Updated"

        # Delete note (set notes to empty list)
        resp = await client.patch(f"/api/trees/{tree_id}", json={
            "notes": json.dumps([]),
        })
        assert resp.status_code == 200

        # Verify deletion
        resp = await client.get("/api/trees")
        trees = resp.json()["trees"]
        tree = [t for t in trees if t["id"] == tree_id][0]
        notes_raw = tree.get("notes", "[]")
        if isinstance(notes_raw, str):
            notes = json.loads(notes_raw)
        else:
            notes = notes_raw
        assert len(notes) == 0


# ---------------------------------------------------------------------------
# TestE2ETreeDeletion
# ---------------------------------------------------------------------------

class TestE2ETreeDeletion:

    @pytest.mark.asyncio
    async def test_delete_tree_removes_all(self, server):
        """Deleting a tree removes it from the list completely."""
        client, project, branch, repo_id = server

        tree_id, root_id = await _create_tree(client, branch, project, repo_id, "Delete tree test")

        # Verify it exists
        resp = await client.get("/api/trees")
        tree_ids = [t["id"] for t in resp.json()["trees"]]
        assert tree_id in tree_ids

        # Delete tree
        resp = await client.delete(f"/api/trees/{tree_id}")
        assert resp.status_code == 200

        # Verify it's gone
        resp = await client.get("/api/trees")
        tree_ids = [t["id"] for t in resp.json()["trees"]]
        assert tree_id not in tree_ids


# ---------------------------------------------------------------------------
# TestE2ECrossViewSync
# ---------------------------------------------------------------------------

class TestE2ECrossViewSync:

    @pytest.mark.asyncio
    async def test_rest_operations_consistent(self, server):
        """REST create + list + delete operations are consistent."""
        client, project, branch, repo_id = server

        # Create tree via REST
        tree_id, root_id = await _create_tree(client, branch, project, repo_id, "Sync test")

        # List should show the tree
        resp = await client.get("/api/trees")
        trees = resp.json()["trees"]
        tree_ids = [t["id"] for t in trees]
        assert tree_id in tree_ids

        # Delete the tree
        resp = await client.delete(f"/api/trees/{tree_id}")
        assert resp.status_code == 200

        # Should no longer appear in list
        resp = await client.get("/api/trees")
        trees = resp.json()["trees"]
        tree_ids = [t["id"] for t in trees]
        assert tree_id not in tree_ids
