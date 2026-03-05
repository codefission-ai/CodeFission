"""Tests for db module — schema creation, migrations, connection handling."""

import pytest

from db import init_db, get_db


@pytest.mark.asyncio
async def test_init_db_creates_tables(tmp_db):
    """init_db creates trees and nodes tables."""
    async with get_db() as db:
        # Check trees table exists
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trees'"
        )
        assert await cursor.fetchone() is not None

        # Check nodes table exists
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nodes'"
        )
        assert await cursor.fetchone() is not None


@pytest.mark.asyncio
async def test_init_db_idempotent(tmp_db):
    """init_db can be called multiple times safely."""
    await init_db()
    await init_db()
    async with get_db() as db:
        cursor = await db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
        row = await cursor.fetchone()
        assert row[0] >= 2


@pytest.mark.asyncio
async def test_schema_has_required_columns(tmp_db):
    """Tables have all expected columns after migration."""
    async with get_db() as db:
        # Trees columns
        cursor = await db.execute("PRAGMA table_info(trees)")
        tree_cols = {row[1] for row in await cursor.fetchall()}
        assert {"id", "name", "created_at", "provider", "model", "repo_mode", "repo_source"} <= tree_cols

        # Nodes columns
        cursor = await db.execute("PRAGMA table_info(nodes)")
        node_cols = {row[1] for row in await cursor.fetchall()}
        expected = {"id", "tree_id", "parent_id", "user_message", "assistant_response",
                    "label", "status", "created_at", "git_branch", "git_commit", "session_id"}
        assert expected <= node_cols


@pytest.mark.asyncio
async def test_wal_mode_enabled(tmp_db):
    """Database uses WAL journal mode for concurrency."""
    async with get_db() as db:
        cursor = await db.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row[0] == "wal"


@pytest.mark.asyncio
async def test_foreign_keys_enabled(tmp_db):
    """Foreign keys are enforced."""
    async with get_db() as db:
        cursor = await db.execute("PRAGMA foreign_keys")
        row = await cursor.fetchone()
        assert row[0] == 1
