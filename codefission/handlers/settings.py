"""Settings and state handler methods — mixin for ConnectionHandler."""

import json

from events import WS
from services.trees import (
    get_setting, set_setting, get_global_defaults,
)


def list_providers() -> list[dict]:
    """Return serializable list of known providers for the frontend.

    This is a static registry -- runtime availability and auth status
    come from agentbridge.discover() on the settings page.
    """
    return [
        {
            "id": "claude-code",
            "name": "Claude Code",
            "models": ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001"],
            "default_model": "claude-opus-4-6",
            "auth_modes": ["cli", "api_key"],
            "default_auth_mode": "cli",
        },
        {
            "id": "codex",
            "name": "Codex CLI",
            "models": ["o4-mini", "codex-mini"],
            "default_model": "o4-mini",
            "auth_modes": ["api_key"],
            "default_auth_mode": "api_key",
        },
        {
            "id": "gemini-cli",
            "name": "Gemini CLI",
            "models": ["gemini-2.5-pro", "gemini-2.5-flash"],
            "default_model": "gemini-2.5-pro",
            "auth_modes": ["api_key", "gcloud"],
            "default_auth_mode": "api_key",
        },
        {
            "id": "aider",
            "name": "Aider",
            "models": ["sonnet", "opus", "gpt-4o", "deepseek"],
            "default_model": "sonnet",
            "auth_modes": ["api_key"],
            "default_auth_mode": "api_key",
        },
    ]


class SettingsMixin:

    async def handle_select_tree(self, data: dict):
        tree_id = data.get("tree_id")
        await set_setting("last_tree_id", tree_id)

    async def handle_set_expanded(self, data: dict):
        node_id = data["node_id"]
        expanded = data["expanded"]
        raw = await get_setting("expanded_nodes")
        nodes_map = json.loads(raw) if raw else {}
        if expanded:
            nodes_map[node_id] = True
        else:
            nodes_map.pop(node_id, None)
        await set_setting("expanded_nodes", json.dumps(nodes_map))

    async def handle_set_subtree_collapsed(self, data: dict):
        node_id = data["node_id"]
        collapsed = data["collapsed"]
        raw = await get_setting("collapsed_subtrees")
        subtrees_map = json.loads(raw) if raw else {}
        if collapsed:
            subtrees_map[node_id] = True
        else:
            subtrees_map.pop(node_id, None)
        await set_setting("collapsed_subtrees", json.dumps(subtrees_map))

    async def handle_get_settings(self, data: dict):  # noqa: ARG002
        defaults = await get_global_defaults()
        await self.send(WS.SETTINGS, global_defaults=defaults, providers=list_providers())

    async def handle_update_global_settings(self, data: dict):
        """Delegate to Orchestrator, format WS response."""
        defaults = await self.orch.update_global_settings(data)
        await self.send(WS.SETTINGS, global_defaults=defaults, providers=list_providers())

    async def handle_update_tree_settings(self, data: dict):
        """Delegate to Orchestrator, format WS response."""
        tree_id = data["tree_id"]
        tree = await self.orch.update_tree_settings(tree_id, data)
        if tree:
            await self.send(WS.TREE_UPDATED, tree=tree.model_dump())
