"""Settings CRUD — get/set global defaults and resolve tree settings.

Fallback provider/model come from agentbridge discovery.
"""

from db import get_db
from models import Tree


async def _get_agentbridge_defaults() -> tuple[str, str]:
    """Ask agentbridge for the first ready provider and its default model.

    Returns (provider_id, default_model). Falls back to ("claude-code", "claude-sonnet-4-6")
    if agentbridge discovery fails or nothing is installed.
    """
    try:
        from agentbridge import discover
        providers = await discover()
        for p in providers:
            if p.ready:
                return p.id, p.default_model
        # Nothing ready — return first installed
        for p in providers:
            if p.installed:
                return p.id, p.default_model
    except Exception:
        pass
    return "claude-code", "claude-sonnet-4-6"


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
    """Return global default settings (from settings table + agentbridge discovery)."""
    provider = await get_setting("default_provider")
    model = await get_setting("default_model")

    # If user hasn't set provider/model, ask agentbridge for the best default
    if not provider or not model:
        ab_provider, ab_model = await _get_agentbridge_defaults()
        if not provider:
            provider = ab_provider
        if not model:
            model = ab_model

    max_turns_raw = await get_setting("default_max_turns")
    max_turns = int(max_turns_raw) if max_turns_raw else 0  # 0 = unlimited
    auth_mode = await get_setting("auth_mode") or "cli"
    api_key = await get_setting("api_key") or ""
    summary_model = await get_setting("summary_model") or "claude-haiku-4-5-20251001"

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
