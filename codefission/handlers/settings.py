"""Settings handlers — get/update global and tree settings, UI state.

Also handles UI-only persistence: expanded nodes, collapsed subtrees,
last selected tree. These are not business operations — just layout state
that happens to be stored in the DB.
"""

import json

from events import WS
from store.settings import (
    get_setting, set_setting, get_global_defaults,
)


async def list_providers() -> list[dict]:
    """Discover installed providers via agentbridge and return serializable list.

    This is the single source of truth for provider info — no hardcoded lists.
    Returns real-time install status, auth status, models, and versions.
    """
    from agentbridge import discover
    providers = await discover()
    return [
        {
            "id": p.id,
            "name": p.name,
            "installed": p.installed,
            "ready": p.ready,
            "version": p.version,
            "models": p.available_models,
            "default_model": p.default_model,
            "auth": [
                {"method": a.method, "authenticated": a.authenticated, "detail": a.detail}
                for a in p.auth
            ],
        }
        for p in providers
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
        providers = await list_providers()
        await self.send(WS.SETTINGS, global_defaults=defaults, providers=providers)

    async def handle_update_global_settings(self, data: dict):
        """Delegate to Orchestrator, format WS response."""
        defaults = await self.orch.update_global_settings(data)
        providers = await list_providers()
        await self.send(WS.SETTINGS, global_defaults=defaults, providers=providers)

    async def handle_update_tree_settings(self, data: dict):
        """Delegate to Orchestrator, format WS response."""
        tree_id = data["tree_id"]
        tree = await self.orch.update_tree_settings(tree_id, data)
        if tree:
            await self.send(WS.TREE_UPDATED, tree=tree.model_dump())
