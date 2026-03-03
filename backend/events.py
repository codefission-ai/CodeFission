"""Async event bus for decoupled communication between services and WebSocket layer.

Adapted from WhatTheBot's core/events.py — same pub/sub pattern, Clawtree-specific events.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine

Callback = Callable[..., Coroutine[Any, Any, None]]


class EventBus:
    def __init__(self) -> None:
        self._listeners: dict[str, list[Callback]] = defaultdict(list)

    def on(self, event: str, callback: Callback) -> None:
        self._listeners[event].append(callback)

    def off(self, event: str, callback: Callback) -> None:
        self._listeners[event] = [
            cb for cb in self._listeners[event] if cb is not callback
        ]

    async def emit(self, event: str, **kwargs: Any) -> None:
        for cb in list(self._listeners.get(event, [])):
            asyncio.create_task(cb(**kwargs))


# Singleton bus
bus = EventBus()


# ── Well-known event names (internal, backend-side) ─────────────────────

STREAM_START = "stream_start"    # chat streaming begins for a node
STREAM_DELTA = "stream_delta"    # new token(s) in a streaming response
STREAM_END = "stream_end"        # chat streaming finished
STREAM_ERROR = "stream_error"    # chat streaming hit an error
NODE_CREATED = "node_created"    # a new node was created
NODE_UPDATED = "node_updated"    # a node's data changed
TREE_CREATED = "tree_created"    # a new tree was created
TREE_DELETED = "tree_deleted"    # a tree was deleted


# ── WebSocket message types (wire protocol, client ↔ server) ───────────

class WS:
    """Structured constants for WebSocket JSON message types.

    Inbound  = client → server requests
    Outbound = server → client responses/pushes
    """

    # Inbound (client → server)
    LIST_TREES = "list_trees"
    CREATE_TREE = "create_tree"
    LOAD_TREE = "load_tree"
    DELETE_TREE = "delete_tree"
    BRANCH = "branch"
    CHAT = "chat"
    GET_NODE = "get_node"

    # Outbound (server → client)
    TREES = "trees"
    TREE_CREATED = "tree_created"
    TREE_LOADED = "tree_loaded"
    TREE_DELETED = "tree_deleted"
    NODE_CREATED = "node_created"
    NODE_DATA = "node_data"
    STATUS = "status"
    CHUNK = "chunk"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    DONE = "done"
    ERROR = "error"
