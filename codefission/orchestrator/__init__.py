"""orchestrator — business workflow coordination layer.

The Orchestrator class coordinates multi-step operations across
store/ modules (DB, git, AI). Each file is a mixin grouped by domain,
mirroring the handler files that call into them.

orchestrator/ only calls store/. Never touches handlers/ or WS directly.
"""

from __future__ import annotations

from models import (
    StreamState,
    ChatNodeCreated,
    ChatCompleted,
    ChatContext,
    ChatResult,
    CancelResult,
    DeleteNodeResult,
    UpdateBaseResult,
    FileListResult,
    DiffResult,
    FileContentResult,
)
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
    """Business-logic coordinator.

    Methods perform multi-step workflows (e.g. create tree = DB + git ref).
    Returns data; the caller (handler) decides how to deliver it.
    """

    def __init__(self):
        self._active_streams: dict[str, StreamState] = {}


__all__ = [
    "Orchestrator",
    "ChatNodeCreated",
    "ChatCompleted",
    "ChatContext",
    "ChatResult",
    "CancelResult",
    "DeleteNodeResult",
    "UpdateBaseResult",
    "FileListResult",
    "DiffResult",
    "FileContentResult",
    "StreamState",
]
