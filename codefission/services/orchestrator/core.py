"""Orchestrator — the business-logic coordinator.

Both the WebSocket handler and future headless agents (shadow, CI) call into
this class instead of scattering logic across the transport layer.
"""

from __future__ import annotations

from services.orchestrator.types import StreamState
from services.orchestrator.chat import ChatMixin
from services.orchestrator.trees import TreesMixin
from services.orchestrator.nodes import NodesMixin
from services.orchestrator.files import FilesMixin
from services.orchestrator.settings import SettingsMixin
from services.orchestrator.repo import RepoMixin


class Orchestrator(
    ChatMixin,
    TreesMixin,
    NodesMixin,
    FilesMixin,
    SettingsMixin,
    RepoMixin,
):
    """Business-logic coordinator — owns all domain logic.

    Methods perform the multi-step workflows that were previously inlined
    in ConnectionHandler. They return data; the caller decides how to
    deliver it (WebSocket, stdout, etc.).
    """

    def __init__(self):
        # Active streams registry — keyed by node_id, used for cancel and reconnect
        self._active_streams: dict[str, StreamState] = {}
