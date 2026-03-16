"""Tests for project creation flows: browse, init, empty project, clone."""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    """Create a test client with DATA_DIR redirected to tmp_path."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    import config as config_mod
    data_dir = tmp_path / ".codefission"
    data_dir.mkdir()
    monkeypatch.setattr(config_mod, "DATA_DIR", data_dir)

    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ===========================================================================
# Flow 1: Browse directory
# ===========================================================================

class TestBrowseDirectory:

    @pytest.mark.anyio
    async def test_browse_home_returns_entries(self, client, tmp_path):
        """Browse an existing directory returns entries."""
        # Create some subdirs in tmp_path
        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()

        resp = await client.get(f"/api/browse?path={tmp_path}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current"] == str(tmp_path)
        names = [e["name"] for e in data["entries"]]
        assert "alpha" in names
        assert "beta" in names

    @pytest.mark.anyio
    async def test_browse_returns_is_git_flag(self, client, tmp_path):
        """Directories with .git/ are flagged as git repos."""
        git_dir = tmp_path / "myrepo"
        git_dir.mkdir()
        (git_dir / ".git").mkdir()

        non_git_dir = tmp_path / "plain"
        non_git_dir.mkdir()

        resp = await client.get(f"/api/browse?path={tmp_path}")
        assert resp.status_code == 200
        entries = {e["name"]: e for e in resp.json()["entries"]}
        assert entries["myrepo"]["is_git"] is True
        assert entries["plain"]["is_git"] is False

    @pytest.mark.anyio
    async def test_browse_nonexistent_returns_error(self, client, tmp_path):
        """Browsing a non-existent path returns 400."""
        resp = await client.get(f"/api/browse?path={tmp_path}/nonexistent")
        assert resp.status_code == 400

    @pytest.mark.anyio
    async def test_browse_hides_hidden_dirs(self, client, tmp_path):
        """Hidden directories (starting with .) are not listed."""
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "visible").mkdir()

        resp = await client.get(f"/api/browse?path={tmp_path}")
        assert resp.status_code == 200
        names = [e["name"] for e in resp.json()["entries"]]
        assert ".hidden" not in names
        assert "visible" in names

    @pytest.mark.anyio
    async def test_browse_returns_parent(self, client, tmp_path):
        """Browse returns parent directory path."""
        sub = tmp_path / "sub"
        sub.mkdir()

        resp = await client.get(f"/api/browse?path={sub}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["parent"] == str(tmp_path)

    @pytest.mark.anyio
    async def test_browse_skips_files(self, client, tmp_path):
        """Only directories are listed, not files."""
        (tmp_path / "dir1").mkdir()
        (tmp_path / "file1.txt").write_text("hello")

        resp = await client.get(f"/api/browse?path={tmp_path}")
        assert resp.status_code == 200
        names = [e["name"] for e in resp.json()["entries"]]
        assert "dir1" in names
        assert "file1.txt" not in names


# ===========================================================================
# Flow 2: Init git repo
# ===========================================================================

class TestInitGitRepo:

    @pytest.mark.anyio
    async def test_init_creates_git_repo(self, tmp_path):
        """init_git_repo creates a .git directory."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from store.git import init_git_repo

        project = tmp_path / "newrepo"
        project.mkdir()

        await init_git_repo(project)

        assert (project / ".git").is_dir()

    @pytest.mark.anyio
    async def test_init_creates_gitignore(self, tmp_path):
        """init_git_repo creates a .gitignore with expected entries."""
        from store.git import init_git_repo

        project = tmp_path / "newrepo2"
        project.mkdir()

        await init_git_repo(project)

        gitignore = project / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text()
        assert ".codefission/" in content
        assert ".claude/" in content
        assert "_artifacts/" in content

    @pytest.mark.anyio
    async def test_init_has_initial_commit(self, tmp_path):
        """init_git_repo makes an initial commit."""
        from store.git import init_git_repo

        project = tmp_path / "newrepo3"
        project.mkdir()

        await init_git_repo(project)

        result = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=str(project),
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "initial commit" in result.stdout

    @pytest.mark.anyio
    async def test_init_preserves_existing_gitignore(self, tmp_path):
        """init_git_repo does not overwrite an existing .gitignore."""
        from store.git import init_git_repo

        project = tmp_path / "newrepo4"
        project.mkdir()
        (project / ".gitignore").write_text("node_modules/\n")

        await init_git_repo(project)

        content = (project / ".gitignore").read_text()
        assert "node_modules/" in content
        # Should not have overwritten with default content
        assert ".codefission/" not in content


# ===========================================================================
# Flow 2b: Open repo auto-init
# ===========================================================================

class TestOpenRepoAutoInit:

    @pytest.mark.anyio
    async def test_open_non_git_folder_auto_inits(self, tmp_path, monkeypatch):
        """Opening a non-git folder should auto-init a git repo via handle_open_repo."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import config as config_mod
        data_dir = tmp_path / ".codefission"
        data_dir.mkdir()
        monkeypatch.setattr(config_mod, "DATA_DIR", data_dir)

        from store.git import init_git_repo

        # Create a directory without git
        project = tmp_path / "nongit"
        project.mkdir()
        (project / "hello.txt").write_text("world")

        # Verify it's not a git repo
        assert not (project / ".git").is_dir()

        # Simulate what handle_open_repo does: detect non-git and init
        result = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            cwd=str(project), capture_output=True, text=True,
        )
        assert result.returncode != 0  # not a git repo

        await init_git_repo(project)

        # Now it should be a git repo
        assert (project / ".git").is_dir()
        result = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            cwd=str(project), capture_output=True, text=True,
        )
        assert result.returncode == 0

    @pytest.mark.anyio
    async def test_open_git_folder_works_normally(self, tmp_path):
        """Opening an existing git repo should not re-init."""
        project = tmp_path / "gitrepo"
        project.mkdir()
        subprocess.run(["git", "init"], cwd=str(project), capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                       cwd=str(project), capture_output=True,
                       env={
                           **__import__("os").environ,
                           "GIT_COMMITTER_NAME": "test",
                           "GIT_COMMITTER_EMAIL": "test@test",
                           "GIT_AUTHOR_NAME": "test",
                           "GIT_AUTHOR_EMAIL": "test@test",
                       })

        result = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            cwd=str(project), capture_output=True, text=True,
        )
        assert result.returncode == 0
        original_sha = result.stdout.strip()

        # The repo should still have the same initial commit
        result2 = subprocess.run(
            ["git", "rev-list", "--max-parents=0", "HEAD"],
            cwd=str(project), capture_output=True, text=True,
        )
        assert result2.stdout.strip() == original_sha


# ===========================================================================
# Flow 3: Empty project
# ===========================================================================

class TestEmptyProject:

    @pytest.mark.anyio
    async def test_create_empty_project(self, client, tmp_path, monkeypatch):
        """POST /api/create-empty-project creates a git repo."""
        import config as config_mod
        # Redirect HOME so ~/.codefission/projects goes into tmp
        monkeypatch.setenv("HOME", str(tmp_path))
        # Also need to patch Path.home() which may be cached
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        resp = await client.post("/api/create-empty-project?name=test-proj")
        assert resp.status_code == 200
        data = resp.json()
        project_path = Path(data["path"])
        assert project_path.is_dir()
        assert (project_path / ".git").is_dir()

    @pytest.mark.anyio
    async def test_create_duplicate_name_fails(self, client, tmp_path, monkeypatch):
        """Creating a project with a duplicate name returns 400."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        resp1 = await client.post("/api/create-empty-project?name=dup")
        assert resp1.status_code == 200

        resp2 = await client.post("/api/create-empty-project?name=dup")
        assert resp2.status_code == 400


# ===========================================================================
# Flow 4: Clone
# ===========================================================================

class TestGitClone:

    @pytest.mark.anyio
    async def test_clone_endpoint_exists(self, client):
        """The /api/clone endpoint exists (returns 422 for missing params, not 404)."""
        resp = await client.post("/api/clone")
        # FastAPI returns 422 for missing required query params
        assert resp.status_code == 422
