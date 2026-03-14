"""Phase 2C — Test REST API routes (CLI Presenter).

Uses FastAPI's httpx-based AsyncClient with ASGITransport to test the
REST API routes defined in main.py. Each test class covers a resource:
  - TreeRoutes: CRUD for trees
  - NodeRoutes: branch, delete, get, files, diff
  - ChatRoutes: SSE streaming, cancel
  - SettingsRoutes: global settings get/patch
  - ProviderRoutes: provider discovery
  - AuditLogRoutes: action log retrieval
  - CrossPresenterNotification: EventBus integration
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock

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


def _tree_body(name, branch, project, repo_id):
    """Build the JSON body for POST /api/trees."""
    return {
        "name": name,
        "base_branch": branch,
        "repo_path": str(project),
        "repo_id": repo_id,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def app_client(tmp_db, tmp_project):
    """Create an AsyncClient backed by the FastAPI app with DB + git initialised."""
    import os

    project = tmp_project
    branch = await _init_project(project)
    repo_id = await _get_repo_id(project)

    # Set env vars that main.py reads on startup
    os.environ["CODEFISSION_REPO_PATH"] = str(project)

    # Import the app after DB init (tmp_db fixture handles init_db)
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, project, branch, repo_id

    # Clean up env
    os.environ.pop("CODEFISSION_REPO_PATH", None)


@pytest_asyncio.fixture
async def tree_with_nodes(app_client):
    """Create a tree with root node, return IDs for tests."""
    client, project, branch, repo_id = app_client

    # Create a tree — response is {"tree": {...}, "root": {...}}
    resp = await client.post("/api/trees", json=_tree_body("Test Tree", branch, project, repo_id))
    assert resp.status_code in (200, 201)
    tree_data = resp.json()
    tree_id = tree_data["tree"]["id"]
    root_id = tree_data["root"]["id"]

    return {
        "client": client,
        "project": project,
        "branch": branch,
        "repo_id": repo_id,
        "tree_id": tree_id,
        "root_id": root_id,
    }


# ---------------------------------------------------------------------------
# TestTreeRoutes
# ---------------------------------------------------------------------------

class TestTreeRoutes:

    @pytest.mark.asyncio
    async def test_post_trees_creates_tree(self, app_client):
        """POST /api/trees creates a tree and returns tree + root."""
        client, project, branch, repo_id = app_client
        resp = await client.post("/api/trees", json=_tree_body("My Tree", branch, project, repo_id))
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert "tree" in data
        assert "id" in data["tree"]
        assert len(data["tree"]["id"]) > 0

    @pytest.mark.asyncio
    async def test_get_trees_lists_all(self, app_client):
        """GET /api/trees returns all created trees."""
        client, project, branch, repo_id = app_client

        # Create 3 trees
        for i in range(3):
            await client.post("/api/trees", json=_tree_body(f"Tree {i}", branch, project, repo_id))

        resp = await client.get("/api/trees")
        assert resp.status_code == 200
        data = resp.json()
        trees = data.get("trees", [])
        assert len(trees) >= 3

    @pytest.mark.asyncio
    async def test_delete_tree(self, app_client):
        """DELETE /api/trees/:id removes the tree."""
        client, project, branch, repo_id = app_client

        # Create tree
        resp = await client.post("/api/trees", json=_tree_body("To Delete", branch, project, repo_id))
        tree_id = resp.json()["tree"]["id"]

        # Delete it
        resp = await client.delete(f"/api/trees/{tree_id}")
        assert resp.status_code == 200

        # Verify it's gone
        resp = await client.get("/api/trees")
        trees = resp.json()["trees"]
        tree_ids = [t["id"] for t in trees]
        assert tree_id not in tree_ids

    @pytest.mark.asyncio
    async def test_patch_tree_updates_settings(self, app_client):
        """PATCH /api/trees/:id updates tree-level settings."""
        client, project, branch, repo_id = app_client

        # Create tree
        resp = await client.post("/api/trees", json=_tree_body("Settings Tree", branch, project, repo_id))
        tree_id = resp.json()["tree"]["id"]

        # Update settings
        resp = await client.patch(f"/api/trees/{tree_id}", json={
            "provider": "codex",
            "model": "o4-mini",
        })
        assert resp.status_code == 200

        # Verify settings persisted — response is {"tree": {...}}
        data = resp.json()
        tree = data["tree"]
        assert tree["provider"] == "codex"
        assert tree["model"] == "o4-mini"


# ---------------------------------------------------------------------------
# TestNodeRoutes
# ---------------------------------------------------------------------------

class TestNodeRoutes:

    @pytest.mark.asyncio
    async def test_post_branch_creates_child(self, tree_with_nodes):
        """POST /api/trees/:id/nodes/:id/branch creates a child node."""
        info = tree_with_nodes
        client = info["client"]

        resp = await client.post(
            f"/api/trees/{info['tree_id']}/nodes/{info['root_id']}/branch",
            json={"label": "try alt"},
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        # Response is {"node": {...}}
        assert "node" in data
        assert data["node"]["parent_id"] == info["root_id"]

    @pytest.mark.asyncio
    async def test_delete_node_removes_subtree(self, tree_with_nodes):
        """DELETE /api/trees/:id/nodes/:id removes the node and children."""
        info = tree_with_nodes
        client = info["client"]

        # Create a child
        resp = await client.post(
            f"/api/trees/{info['tree_id']}/nodes/{info['root_id']}/branch",
            json={"label": "to delete"},
        )
        child_id = resp.json()["node"]["id"]

        # Delete it
        resp = await client.delete(
            f"/api/trees/{info['tree_id']}/nodes/{child_id}",
        )
        assert resp.status_code == 200

        # Verify node is gone
        resp = await client.get(
            f"/api/trees/{info['tree_id']}/nodes/{child_id}",
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_node_returns_details(self, tree_with_nodes):
        """GET /api/trees/:id/nodes/:id returns node details."""
        info = tree_with_nodes
        client = info["client"]

        resp = await client.get(
            f"/api/trees/{info['tree_id']}/nodes/{info['root_id']}",
        )
        assert resp.status_code == 200
        data = resp.json()
        # Response is {"node": {...}}
        node = data["node"]
        assert node["id"] == info["root_id"]
        assert "status" in node

    @pytest.mark.asyncio
    async def test_get_node_files(self, tree_with_nodes):
        """GET /api/trees/:id/nodes/:id/files returns file list."""
        info = tree_with_nodes
        client = info["client"]

        resp = await client.get(
            f"/api/trees/{info['tree_id']}/nodes/{info['root_id']}/files",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("files"), list)

    @pytest.mark.asyncio
    async def test_get_node_diff(self, tree_with_nodes):
        """GET /api/trees/:id/nodes/:id/diff returns unified diff."""
        info = tree_with_nodes
        client = info["client"]

        resp = await client.get(
            f"/api/trees/{info['tree_id']}/nodes/{info['root_id']}/diff",
        )
        assert resp.status_code == 200
        data = resp.json()
        # Diff may be empty for root node, but "diff" key should exist
        assert "diff" in data


# ---------------------------------------------------------------------------
# TestChatRoutes
# ---------------------------------------------------------------------------

class TestChatRoutes:

    @pytest.mark.asyncio
    async def test_post_chat_streams_sse(self, tree_with_nodes):
        """POST /api/trees/:id/nodes/:id/chat returns SSE stream."""
        info = tree_with_nodes
        client = info["client"]

        # The chat route streams SSE via the orchestrator.
        from services.orchestrator import Orchestrator, ChatNodeCreated, ChatCompleted, ChatResult
        from services.chat import TextDelta, SessionInit

        async def mock_chat(self_orch, node_id, message, **kwargs):
            from services.trees import create_child_node, update_node, get_node as _get
            child = await create_child_node(node_id, "")
            await update_node(child.id, user_message=message, status="active")
            child = await _get(child.id)
            yield ChatNodeCreated(node=child)
            yield SessionInit(session_id="test-sess")
            yield TextDelta(text="Hello world")
            await update_node(child.id, status="done", assistant_response="Hello world")
            yield ChatCompleted(result=ChatResult(
                node_id=child.id,
                full_response="Hello world",
                git_commit=None,
                files_changed=0,
            ))

        with patch.object(Orchestrator, "chat", mock_chat):
            resp = await client.post(
                f"/api/trees/{info['tree_id']}/nodes/{info['root_id']}/chat",
                json={"message": "hello"},
            )

        # SSE response should be successful
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        assert "text/event-stream" in content_type

    @pytest.mark.asyncio
    async def test_post_cancel_no_stream(self, tree_with_nodes):
        """POST /api/trees/:id/nodes/:id/cancel returns 404 when no active stream."""
        info = tree_with_nodes
        client = info["client"]

        # Create a branch node
        resp = await client.post(
            f"/api/trees/{info['tree_id']}/nodes/{info['root_id']}/branch",
            json={"label": "to cancel"},
        )
        node_id = resp.json()["node"]["id"]

        # Cancel it — no active stream, should return 404
        resp = await client.post(
            f"/api/trees/{info['tree_id']}/nodes/{node_id}/cancel",
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TestSettingsRoutes
# ---------------------------------------------------------------------------

class TestSettingsRoutes:

    @pytest.mark.asyncio
    async def test_get_settings(self, app_client):
        """GET /api/settings returns global defaults and providers."""
        client, _, _, _ = app_client
        resp = await client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "global_defaults" in data
        assert "providers" in data

    @pytest.mark.asyncio
    async def test_patch_settings(self, app_client):
        """PATCH /api/settings updates and returns new settings."""
        client, _, _, _ = app_client

        resp = await client.patch("/api/settings", json={
            "default_provider": "codex",
        })
        assert resp.status_code == 200

        # Verify change — global_defaults uses short key names (provider, model, etc.)
        resp = await client.get("/api/settings")
        data = resp.json()
        defaults = data["global_defaults"]
        assert defaults.get("provider") == "codex"


# ---------------------------------------------------------------------------
# TestProviderRoutes
# ---------------------------------------------------------------------------

class TestProviderRoutes:

    @pytest.mark.asyncio
    async def test_get_providers(self, app_client):
        """GET /api/providers returns provider list."""
        client, _, _, _ = app_client

        resp = await client.get("/api/providers")
        assert resp.status_code == 200
        data = resp.json()
        # Response is {"providers": [...]}
        providers = data["providers"]
        assert isinstance(providers, list)
        assert len(providers) > 0
        first = providers[0]
        assert "id" in first
        assert "name" in first


# ---------------------------------------------------------------------------
# TestAuditLogRoutes
# ---------------------------------------------------------------------------

class TestAuditLogRoutes:

    @pytest.mark.asyncio
    async def test_get_log(self, tree_with_nodes):
        """GET /api/trees/:id/log returns action log entries."""
        info = tree_with_nodes
        client = info["client"]

        # Create some activity: branch from root
        await client.post(
            f"/api/trees/{info['tree_id']}/nodes/{info['root_id']}/branch",
            json={"label": "branch for log"},
        )

        resp = await client.get(f"/api/trees/{info['tree_id']}/log")
        assert resp.status_code == 200
        data = resp.json()
        # Response is {"actions": [...]}
        actions = data["actions"]
        assert isinstance(actions, list)
        if len(actions) > 0:
            seqs = [a.get("seq") for a in actions if a.get("seq") is not None]
            assert seqs == sorted(seqs)


# ---------------------------------------------------------------------------
# TestCrossPresenterNotification
# ---------------------------------------------------------------------------

class TestCrossPresenterNotification:

    @pytest.mark.asyncio
    async def test_rest_create_tree_succeeds(self, app_client):
        """Creating a tree via REST succeeds and returns the tree data."""
        client, project, branch, repo_id = app_client

        resp = await client.post("/api/trees", json=_tree_body("Cross Presenter Test", branch, project, repo_id))
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert "tree" in data
        assert data["tree"]["name"] == "Cross Presenter Test"
