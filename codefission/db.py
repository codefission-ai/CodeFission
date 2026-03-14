import aiosqlite
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from models import DEFAULT_PROVIDER, DEFAULT_MODEL
from config import get_global_db_path

log = logging.getLogger(__name__)

# Cache of open DB connections, keyed by absolute path string
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
    """Create tables and run migrations for the global DB."""
    # Close existing connection to force re-open (e.g. tests changing DB path)
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

        # Migrate: add max_turns to trees
        if "max_turns" not in columns:
            await db.execute("ALTER TABLE trees ADD COLUMN max_turns INTEGER")

        # Migrate: add skill to trees
        if "skill" not in columns:
            await db.execute("ALTER TABLE trees ADD COLUMN skill TEXT NOT NULL DEFAULT ''")

        # Migrate: add notes to trees
        if "notes" not in columns:
            await db.execute("ALTER TABLE trees ADD COLUMN notes TEXT NOT NULL DEFAULT '[]'")

        # Migrate: add base_branch, base_commit to trees
        if "base_branch" not in columns:
            await db.execute("ALTER TABLE trees ADD COLUMN base_branch TEXT NOT NULL DEFAULT 'main'")
        if "base_commit" not in columns:
            await db.execute("ALTER TABLE trees ADD COLUMN base_commit TEXT")

        # Migrate: add repo_id, repo_path, repo_name to trees
        if "repo_id" not in columns:
            await db.execute("ALTER TABLE trees ADD COLUMN repo_id TEXT")
        if "repo_path" not in columns:
            await db.execute("ALTER TABLE trees ADD COLUMN repo_path TEXT")
        if "repo_name" not in columns:
            await db.execute("ALTER TABLE trees ADD COLUMN repo_name TEXT")

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
        if "provider" not in node_columns:
            await db.execute("ALTER TABLE nodes ADD COLUMN provider TEXT")
        if "model" not in node_columns:
            await db.execute("ALTER TABLE nodes ADD COLUMN model TEXT")

        # Index for repo_id + base_commit lookups
        await db.execute("CREATE INDEX IF NOT EXISTS idx_trees_repo ON trees(repo_id, base_commit)")

        # Actions table (audit log) — no FK so entries survive tree deletion
        await db.executescript("""
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
            CREATE INDEX IF NOT EXISTS idx_actions_tree ON actions(tree_id, seq);
        """)

        await db.commit()
