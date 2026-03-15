"""Settings operations — update global defaults and per-tree overrides."""

from __future__ import annotations

from models import Tree
from store.trees import (
    get_tree,
    update_tree,
)
from store.settings import (
    set_setting,
    get_global_defaults,
    set_provider_api_key,
    get_provider_api_key,
)


class SettingsMixin:
    """Global and tree-level settings operations."""

    # ── Data accessors (thin wrappers over store) ────────────────────

    async def get_setting(self, key: str) -> str | None:
        from store.settings import get_setting
        return await get_setting(key)

    async def set_setting(self, key: str, value: str | None) -> None:
        await set_setting(key, value)

    async def get_global_defaults(self) -> dict:
        return await get_global_defaults()

    # ── Mutations ────────────────────────────────────────────────────

    async def update_global_settings(self, data: dict) -> dict:
        """Update global settings. Returns updated global defaults dict."""
        for key in ("default_provider", "default_model", "api_key", "summary_model"):
            if key in data:
                val = data[key]
                await set_setting(key, str(val) if val is not None and val != "" else None)
        # data_dir is saved to config file (requires restart)
        if "data_dir" in data and data["data_dir"]:
            from config import save_config
            save_config({"data_dir": data["data_dir"]})
        # Handle per-provider API keys: "provider_api_keys": {"claude-code": "sk-...", "codex": "sk-..."}
        if "provider_api_keys" in data:
            for provider_id, key in data["provider_api_keys"].items():
                await set_provider_api_key(provider_id, key if key else None)

        return await get_global_defaults()

    async def update_tree_settings(self, tree_id: str, data: dict) -> Tree | None:
        """Update tree-level settings. Returns updated tree."""
        updates = {}
        if "provider" in data:
            updates["provider"] = data["provider"] or ""
        if "model" in data:
            updates["model"] = data["model"] or ""
        if "skill" in data:
            updates["skill"] = data["skill"] or ""
        if "notes" in data:
            updates["notes"] = data["notes"]
        if updates:
            await update_tree(tree_id, **updates)
        return await get_tree(tree_id)
