"""handlers — WS transport layer.

Translates WebSocket JSON messages into orchestrator calls and formats
results back as WS responses. Each file is a mixin for ConnectionHandler,
grouped by domain: chat, trees, nodes, files, settings, repo, processes.

handlers/ only calls orchestrator/. Never touches store/ directly.
"""

from handlers.connection import ConnectionHandler, _active_streams
from handlers.settings import list_providers

__all__ = ["ConnectionHandler", "_active_streams", "list_providers"]
