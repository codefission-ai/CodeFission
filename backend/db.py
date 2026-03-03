import aiosqlite
from pathlib import Path
from contextlib import asynccontextmanager

from models import DEFAULT_PROVIDER, DEFAULT_MODEL

DB_PATH = Path(__file__).parent.parent / "data" / "clawtree.db"


@asynccontextmanager
async def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
    finally:
        await db.close()


async def init_db():
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
        await db.commit()
