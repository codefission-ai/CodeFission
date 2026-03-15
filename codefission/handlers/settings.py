"""Settings handlers — get/update global and tree settings, UI state.

Also handles UI-only persistence: last selected tree. These are not
business operations — just layout state that happens to be stored in the DB.
"""

from events import WS


async def list_providers() -> list[dict]:
    """Discover installed providers via agentbridge and return serializable list.

    Adapts agentbridge's ProviderInfo to the shape the frontend expects:
    - id, name, models, default_model (direct from agentbridge)
    - auth_modes: list of unique auth method strings (e.g. ["cli", "api_key"])
    - default_auth_mode: first authenticated method, or first method
    - installed, ready, version, auth: extra detail from agentbridge
    """
    from agentbridge import discover
    providers = await discover()

    result = []
    for p in providers:
        # Build auth_modes list from agentbridge auth info
        # Simplify method names: "cli_oauth (web)" -> "cli", "api_key" -> "api_key"
        auth_modes = []
        for a in p.auth:
            mode = "cli" if a.method.startswith("cli_oauth") else a.method
            if mode not in auth_modes:
                auth_modes.append(mode)

        # Default auth mode: first authenticated one, or first available
        default_auth_mode = auth_modes[0] if auth_modes else "cli"
        for a in p.auth:
            if a.authenticated:
                default_auth_mode = "cli" if a.method.startswith("cli_oauth") else a.method
                break

        result.append({
            "id": p.id,
            "name": p.name,
            "installed": p.installed,
            "ready": p.ready,
            "version": p.version,
            "models": p.available_models,
            "default_model": p.default_model,
            "auth_modes": auth_modes,
            "default_auth_mode": default_auth_mode,
            "auth": [
                {"method": a.method, "authenticated": a.authenticated, "detail": a.detail}
                for a in p.auth
            ],
        })
    return result


class SettingsMixin:

    async def handle_select_tree(self, data: dict):
        tree_id = data.get("tree_id")
        await self.orch.set_setting("last_tree_id", tree_id)

    async def handle_get_settings(self, data: dict):  # noqa: ARG002
        defaults = await self.orch.get_global_defaults()
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
