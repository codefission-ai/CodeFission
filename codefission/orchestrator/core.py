"""Orchestrator class — assembles all workflow mixins.

The Orchestrator inherits from ChatMixin, TreesMixin, NodesMixin,
FilesMixin, SettingsMixin, RepoMixin. Each mixin is in a sibling file.
Handlers call Orchestrator methods; the Orchestrator calls store/ modules.
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
