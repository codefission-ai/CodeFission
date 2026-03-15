"""Settings CRUD — get/set global defaults and resolve tree settings.

Fallback provider/model/summary_model come from agentbridge discovery + pricing.
Per-provider API keys stored as "api_key:{provider_id}" in the settings table.
"""

from db import get_db
from models import Tree


async def _get_agentbridge_defaults() -> tuple[str, str, str]:
    """Ask agentbridge for the first ready provider, its default model, and cheapest model.

    Returns (provider_id, default_model, cheapest_model).
    Falls back to ("claude-code", "claude-sonnet-4-6", "claude-haiku-4-5-20251001").
    """
    try:
        from agentbridge import discover, cheapest_model
        providers = await discover()

        # Find best provider
        provider = None
        for p in providers:
            if p.ready:
                provider = p
                break
        if not provider:
            for p in providers:
                if p.installed:
                    provider = p
                    break

        if provider:
            # Cheapest model across all known models (for summary/auto-naming)
            all_models = []
            for p in providers:
                all_models.extend(p.available_models)
            cheap = cheapest_model(all_models) or provider.available_models[-1] if provider.available_models else None
            return provider.id, provider.default_model, cheap or "claude-haiku-4-5-20251001"
    except Exception:
        pass
    return "claude-code", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"


# ── Basic CRUD ──────────────────────────────────────────────────────


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


# ── Per-provider API keys ───────────────────────────────────────────


async def get_provider_api_key(provider_id: str) -> str | None:
    """Get the CodeFission-specific API key for a provider.

    Stored as "api_key:{provider_id}" in the settings table.
    This overrides any env var API key when set.
    """
    return await get_setting(f"api_key:{provider_id}")


async def set_provider_api_key(provider_id: str, key: str | None):
    """Set or clear the CodeFission-specific API key for a provider."""
    await set_setting(f"api_key:{provider_id}", key)


async def get_effective_api_key(provider_id: str) -> str:
    """Get the API key to use for a provider.

    Priority: CodeFission per-provider key > global api_key setting > empty string.
    """
    # 1. Per-provider key (set in CodeFission settings UI)
    per_provider = await get_provider_api_key(provider_id)
    if per_provider:
        return per_provider

    # 2. Global api_key setting (legacy, shared across providers)
    global_key = await get_setting("api_key")
    if global_key:
        return global_key

    return ""


# ── Global defaults ─────────────────────────────────────────────────


async def get_global_defaults() -> dict:
    """Return global default settings (from settings table + agentbridge discovery)."""
    provider = await get_setting("default_provider")
    model = await get_setting("default_model")
    summary_model = await get_setting("summary_model")

    # If user hasn't set these, ask agentbridge for the best defaults
    if not provider or not model or not summary_model:
        ab_provider, ab_model, ab_cheap = await _get_agentbridge_defaults()
        if not provider:
            provider = ab_provider
        if not model:
            model = ab_model
        if not summary_model:
            summary_model = ab_cheap

    auth_mode = await get_setting("auth_mode") or "cli"
    api_key = await get_setting("api_key") or ""

    from config import get_global_db_path
    return {
        "provider": provider,
        "model": model,
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
    }
