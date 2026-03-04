"""Shared fixtures for RepoEvolve tests."""

import sys
from pathlib import Path

import pytest
import pytest_asyncio

# Make backend importable
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


@pytest.fixture
def tmp_workspaces(tmp_path, monkeypatch):
    """Redirect WORKSPACES_DIR to a temp directory."""
    import services.workspace_service as ws_mod
    monkeypatch.setattr(ws_mod, "WORKSPACES_DIR", tmp_path)
    return tmp_path


@pytest_asyncio.fixture
async def tmp_db(tmp_path):
    """Use a temporary SQLite DB for tree/node tests."""
    import db as db_mod
    original = db_mod.DB_PATH
    db_mod.DB_PATH = tmp_path / "test.db"
    await db_mod.init_db()
    yield db_mod.DB_PATH
    db_mod.DB_PATH = original
