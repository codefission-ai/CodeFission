"""Async event bus for decoupled communication between services and WebSocket layer.

Adapted from WhatTheBot's core/events.py — same pub/sub pattern, CodeFission-specific events.
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

    # Inbound (client → server) — Repo management
    OPEN_REPO = "open_repo"

    # Inbound (client → server) — Tree/Node operations
    LIST_TREES = "list_trees"
    CREATE_TREE = "create_tree"
    LOAD_TREE = "load_tree"
    DELETE_TREE = "delete_tree"
    BRANCH = "branch"
    CHAT = "chat"
    CANCEL = "cancel"
    DUPLICATE = "duplicate"
    GET_NODE = "get_node"
    GET_NODE_FILES = "get_node_files"
    GET_NODE_DIFF = "get_node_diff"
    GET_FILE_CONTENT = "get_file_content"
    SELECT_TREE = "select_tree"
    SET_EXPANDED = "set_expanded"
    SET_SUBTREE_COLLAPSED = "set_subtree_collapsed"
    GET_SETTINGS = "get_settings"
    UPDATE_GLOBAL_SETTINGS = "update_global_settings"
    UPDATE_TREE_SETTINGS = "update_tree_settings"
    GET_NODE_PROCESSES = "get_node_processes"
    KILL_PROCESS = "kill_process"
    KILL_ALL_PROCESSES = "kill_all_processes"
    DELETE_NODE = "delete_node"
    GET_REPO_INFO = "get_repo_info"
    LIST_BRANCHES = "list_branches"
    MERGE_TO_BRANCH = "merge_to_branch"
    UPDATE_BASE = "update_base"

    # Outbound — Settings
    SETTINGS = "settings"

    # Outbound (server → client) — Repo
    REPO_OPENED = "repo_opened"

    # Outbound (server → client) — Tree/Node
    TREES = "trees"
    TREE_CREATED = "tree_created"
    TREE_LOADED = "tree_loaded"
    TREE_DELETED = "tree_deleted"
    TREE_UPDATED = "tree_updated"
    NODE_CREATED = "node_created"
    NODE_DATA = "node_data"
    NODE_FILES = "node_files"
    NODE_DIFF = "node_diff"
    FILE_CONTENT = "file_content"
    STATUS = "status"
    CHUNK = "chunk"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    DONE = "done"
    ERROR = "error"
    NODE_PROCESSES = "node_processes"
    NODES_DELETED = "nodes_deleted"
    REPO_INFO = "repo_info"
    BRANCHES = "branches"
    MERGE_RESULT = "merge_result"
    BASE_UPDATED = "base_updated"
