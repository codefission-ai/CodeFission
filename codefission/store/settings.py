"""Settings CRUD — get/set global defaults and resolve tree settings."""

from db import get_db
from models import Tree

# Provider defaults — no longer depend on the deleted providers/ package
_FALLBACK_PROVIDER = "claude-code"
_FALLBACK_MODEL = "claude-sonnet-4-6"
_FALLBACK_AUTH_MODE = "cli"


async def get_setting(key: str) -> str | None:
    async with get_db() as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row["value"] if row else None


async def set_setting(key: str, value: str | None):
    async with get_db() as db:
        if value is None:
            await db.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                (key, value, value),
            )
        await db.commit()


async def get_global_defaults() -> dict:
    """Return global default settings (from settings table + provider registry)."""
    provider = await get_setting("default_provider") or _FALLBACK_PROVIDER
    model = await get_setting("default_model") or _FALLBACK_MODEL
    max_turns_raw = await get_setting("default_max_turns")
    max_turns = int(max_turns_raw) if max_turns_raw else 0  # 0 = unlimited
    auth_mode = await get_setting("auth_mode") or _FALLBACK_AUTH_MODE
    api_key = await get_setting("api_key") or ""
    summary_model = await get_setting("summary_model") or "claude-haiku-4-5-20251001"
j
    from config import get_global_db_path
    return {
        "provider": provider,
        "model": model,
        "max_turns": max_turns,
        "auth_mode": auth_mode,
        "api_key": api_key,
        "summary_model": summary_model,
        "data_dir": str(get_global_db_path().parent),
    }


async def resolve_tree_settings(tree: Tree) -> dict:
    """Merge tree overrides with global defaults. Empty string / None = inherit."""
    defaults = await get_global_defaults()
    return {
        "provider": tree.provider if tree.provider else defaults["provider"],
        "model": tree.model if tree.model else defaults["model"],
        "max_turns": tree.max_turns if tree.max_turns is not None else defaults["max_turns"],
    }
