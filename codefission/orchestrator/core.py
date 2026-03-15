"""Orchestrator — the business-logic coordinator.

Both the WebSocket handler and future headless agents (shadow, CI) call into
this class instead of scattering logic across the transport layer.
"""

from __future__ import annotations

from models import StreamState
from orchestrator.chat import ChatMixin
from orchestrator.trees import TreesMixin
from orchestrator.nodes import NodesMixin
from orchestrator.files import FilesMixin
from orchestrator.settings import SettingsMixin
from orchestrator.repo import RepoMixin


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
