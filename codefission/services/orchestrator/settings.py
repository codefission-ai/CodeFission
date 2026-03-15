"""Settings mixin for the Orchestrator."""

from __future__ import annotations

from models import Tree
from services.trees import (
    get_tree,
    update_tree,
    set_setting,
    get_global_defaults,
)


class SettingsMixin:
    """Global and tree-level settings operations."""

    async def update_global_settings(self, data: dict) -> dict:
        """Update global settings. Returns updated global defaults dict."""
        for key in ("default_provider", "default_model", "default_max_turns", "auth_mode", "api_key", "summary_model"):
            if key in data:
                val = data[key]
                await set_setting(key, str(val) if val is not None and val != "" else None)
        # data_dir is saved to config file (requires restart)
        if "data_dir" in data and data["data_dir"]:
            from config import save_config
            save_config({"data_dir": data["data_dir"]})
        return await get_global_defaults()

    async def update_tree_settings(self, tree_id: str, data: dict) -> Tree | None:
        """Update tree-level settings. Returns updated tree."""
        updates = {}
        if "provider" in data:
            updates["provider"] = data["provider"] or ""
        if "model" in data:
            updates["model"] = data["model"] or ""
        if "max_turns" in data:
            updates["max_turns"] = data["max_turns"]
        if "skill" in data:
            updates["skill"] = data["skill"] or ""
        if "notes" in data:
            updates["notes"] = data["notes"]
        if updates:
            await update_tree(tree_id, **updates)
        return await get_tree(tree_id)
