import aiosqlite
from pathlib import Path
from contextlib import asynccontextmanager

from models import DEFAULT_PROVIDER, DEFAULT_MODEL
from config import DATA_DIR

DB_PATH = DATA_DIR / "repoevolve.db"

_conn: aiosqlite.Connection | None = None


async def _open_connection() -> aiosqlite.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


@asynccontextmanager
async def get_db():
    global _conn
    if _conn is None:
        _conn = await _open_connection()
    yield _conn


async def close_db():
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


async def init_db():
    global _conn
    # Close existing connection (e.g. tests changing DB_PATH)
    if _conn is not None:
        await _conn.close()
        _conn = None

    async with get_db() as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS trees (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                tree_id TEXT NOT NULL REFERENCES trees(id),
                parent_id TEXT REFERENCES nodes(id),
                user_message TEXT NOT NULL DEFAULT '',
                assistant_response TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'idle',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_tree ON nodes(tree_id);
            CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
        """)

        # Migrate: add provider/model columns to trees if missing
        cursor = await db.execute("PRAGMA table_info(trees)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "provider" not in columns:
            await db.execute(
                f"ALTER TABLE trees ADD COLUMN provider TEXT NOT NULL DEFAULT '{DEFAULT_PROVIDER}'"
            )
        if "model" not in columns:
            await db.execute(
                f"ALTER TABLE trees ADD COLUMN model TEXT NOT NULL DEFAULT '{DEFAULT_MODEL}'"
            )

        # Migrate: add repo_mode, repo_source to trees
        if "repo_mode" not in columns:
            await db.execute("ALTER TABLE trees ADD COLUMN repo_mode TEXT NOT NULL DEFAULT 'none'")
        if "repo_source" not in columns:
            await db.execute("ALTER TABLE trees ADD COLUMN repo_source TEXT")

        # Migrate: add max_turns to trees
        if "max_turns" not in columns:
            await db.execute("ALTER TABLE trees ADD COLUMN max_turns INTEGER")

        # Migrate: add skill to trees
        if "skill" not in columns:
            await db.execute("ALTER TABLE trees ADD COLUMN skill TEXT NOT NULL DEFAULT ''")

        # Migrate: rename provider "anthropic" → "claude-code"
        await db.execute("UPDATE trees SET provider = 'claude-code' WHERE provider = 'anthropic'")

        # Migrate: add git_branch, git_commit to nodes
        cursor2 = await db.execute("PRAGMA table_info(nodes)")
        node_columns = {row[1] for row in await cursor2.fetchall()}
        if "git_branch" not in node_columns:
            await db.execute("ALTER TABLE nodes ADD COLUMN git_branch TEXT")
        if "git_commit" not in node_columns:
            await db.execute("ALTER TABLE nodes ADD COLUMN git_commit TEXT")
        if "session_id" not in node_columns:
            await db.execute("ALTER TABLE nodes ADD COLUMN session_id TEXT")
        if "created_by" not in node_columns:
            await db.execute("ALTER TABLE nodes ADD COLUMN created_by TEXT NOT NULL DEFAULT 'human'")
        if "quoted_node_ids" not in node_columns:
            await db.execute("ALTER TABLE nodes ADD COLUMN quoted_node_ids TEXT NOT NULL DEFAULT '[]'")

        await db.commit()
