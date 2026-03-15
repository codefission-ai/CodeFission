"""Settings CRUD — get/set global defaults and resolve tree settings.

Fallback provider/model/summary_model come from agentbridge discovery + pricing.
Per-provider API keys stored as "api_key:{provider_id}" in the settings table.
"""

from db import get_db
from models import Tree


async def _get_default_provider() -> tuple[str, str]:
    """First ready provider and its default model from agentbridge.

    Returns (provider_id, default_model).
    """
    try:
        from agentbridge import discover
        providers = await discover()
        for p in providers:
            if p.ready:
                return p.id, p.default_model
        for p in providers:
            if p.installed:
                return p.id, p.default_model
    except Exception:
        pass
    return "claude-code", "claude-sonnet-4-6"


async def _get_cheapest_model_for_provider(provider_id: str) -> str:
    """Cheapest model for a specific provider from agentbridge pricing.

    Only looks at models belonging to the given provider — doesn't
    cross provider boundaries. If the user chose Codex, they get
    Codex's cheapest model, not Claude's.
    """
    try:
        from agentbridge import discover, cheapest_model
        providers = await discover()
        for p in providers:
            if p.id == provider_id and p.available_models:
                cheap = cheapest_model(p.available_models)
                return cheap or p.available_models[-1]
    except Exception:
        pass
    return "claude-sonnet-4-6"


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

    # If user hasn't set provider/model, discover from agentbridge
    if not provider or not model:
        ab_provider, ab_model = await _get_default_provider()
        if not provider:
            provider = ab_provider
        if not model:
            model = ab_model

    # Summary model defaults to cheapest for the SELECTED provider
    if not summary_model:
        summary_model = await _get_cheapest_model_for_provider(provider)

    api_key = await get_setting("api_key") or ""

    from config import get_global_db_path
    return {
        "provider": provider,
        "model": model,
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
