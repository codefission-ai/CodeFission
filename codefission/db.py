"""SQLite database — connection management and schema creation.

Tables: trees, nodes, settings, actions.
Uses aiosqlite for async access. Schema created on first startup.
"""

import aiosqlite
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from config import get_global_db_path

log = logging.getLogger(__name__)

_connections: dict[str, aiosqlite.Connection] = {}


async def _open_connection(db_path: Path) -> aiosqlite.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


@asynccontextmanager
async def get_db():
    """Get the global DB connection."""
    db_path = get_global_db_path()
    key = str(db_path)
    if key not in _connections:
        _connections[key] = await _open_connection(db_path)
    yield _connections[key]


async def close_db():
    """Close all cached DB connections."""
    for key, conn in list(_connections.items()):
        try:
            await conn.close()
        except Exception:
            pass
    _connections.clear()


async def init_db():
    """Create tables if they don't exist."""
    db_path = get_global_db_path()
    key = str(db_path)
    conn = _connections.pop(key, None)
    if conn:
        await conn.close()

    async with get_db() as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS trees (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                skill TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '[]',
                base_branch TEXT NOT NULL DEFAULT 'main',
                base_commit TEXT,
                repo_id TEXT,
                repo_path TEXT,
                repo_name TEXT
            );

            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                tree_id TEXT NOT NULL REFERENCES trees(id),
                parent_id TEXT REFERENCES nodes(id),
                user_message TEXT NOT NULL DEFAULT '',
                assistant_response TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'idle',
                created_at TEXT NOT NULL,
                git_branch TEXT,
                git_commit TEXT,
                session_id TEXT,
                created_by TEXT NOT NULL DEFAULT 'human',
                quoted_node_ids TEXT NOT NULL DEFAULT '[]',
                provider TEXT,
                model TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY,
                seq INTEGER UNIQUE,
                ts TEXT NOT NULL,
                tree_id TEXT,
                node_id TEXT,
                kind TEXT NOT NULL,
                params TEXT NOT NULL DEFAULT '{}',
                result TEXT NOT NULL DEFAULT '{}',
                source TEXT NOT NULL DEFAULT 'gui'
            );

            CREATE INDEX IF NOT EXISTS idx_nodes_tree ON nodes(tree_id);
            CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
            CREATE INDEX IF NOT EXISTS idx_trees_repo ON trees(repo_id, base_commit);
            CREATE INDEX IF NOT EXISTS idx_actions_tree ON actions(tree_id, seq);
        """)
        await db.commit()
