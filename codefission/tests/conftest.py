"""Shared fixtures for CodeFission tests."""

import pytest
import pytest_asyncio

from config import set_project_path


@pytest.fixture
def tmp_project(tmp_path, monkeypatch):
    """Create a temporary git project and redirect all project-local paths.

    Returns the project root (a bare git repo with one commit).
    Uses context var so get_project_path() / get_project_dir() work everywhere.
    Monkeypatches DATA_DIR so the global DB is created in the temp dir.
    """
    project = tmp_path / "project"
    project.mkdir()

    # Set context var — all downstream code (get_project_path, get_project_dir,
    # _worktrees_dir, _artifacts_dir, etc.) reads from this.
    set_project_path(project)

    # Redirect global DB to temp dir so tests don't pollute ~/.codefission
    data_dir = tmp_path / ".codefission"
    data_dir.mkdir()
    import config as config_mod
    monkeypatch.setattr(config_mod, "DATA_DIR", data_dir)

    return project


@pytest.fixture
def tmp_workspaces(tmp_project):
    """Alias for backward-compat — returns the project root."""
    return tmp_project


@pytest_asyncio.fixture
async def tmp_db(tmp_project):
    """Use a temporary SQLite DB for tree/node tests.

    Relies on tmp_project setting DATA_DIR, so get_global_db_path() returns
    the temp dir's DB path.
    """
    import db as db_mod
    await db_mod.init_db()
    yield
    await db_mod.close_db()
