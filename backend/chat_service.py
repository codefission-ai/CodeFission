"""Chat service — uses Claude Agent SDK (subscription or API key).

Adapted from WhatTheBot's bots/bot.py approach: ClaudeSDKClient with
session resume, streaming events, and USE_API toggle.
"""

from __future__ import annotations

import os
import logging
from typing import AsyncGenerator

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, AssistantMessage
from claude_agent_sdk.types import StreamEvent

from tree_service import get_path_to_root, get_tree, get_node

log = logging.getLogger(__name__)

# When True, uses ANTHROPIC_API_KEY; when False, uses Claude Code subscription.
USE_API = False


def _sdk_env() -> dict[str, str]:
    """Build env dict for the SDK subprocess."""
    env = {"CLAUDECODE": ""}
    if USE_API:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if key:
            env["ANTHROPIC_API_KEY"] = key
    return env


def _build_system_prompt(path_nodes) -> str:
    """Build system prompt with conversation history from tree path."""
    parts = ["You are a helpful AI assistant in Clawtree, a tree-structured conversation tool. Be concise and helpful."]

    # Include ancestor conversation as context
    ancestors = path_nodes[:-1] if len(path_nodes) > 1 else []
    if ancestors:
        history_parts = []
        for node in ancestors:
            if node.user_message:
                history_parts.append(f"User: {node.user_message}")
            if node.assistant_response:
                history_parts.append(f"Assistant: {node.assistant_response}")
        if history_parts:
            parts.append(f"\n\nConversation history (continue naturally from here):\n\n" + "\n\n".join(history_parts))

    return "".join(parts)


async def stream_chat(node_id: str, user_message: str) -> AsyncGenerator[str, None]:
    """Stream a chat response for a node using Claude Agent SDK."""
    node = await get_node(node_id)
    if not node:
        return

    tree = await get_tree(node.tree_id)
    if not tree:
        return

    path = await get_path_to_root(node_id)
    system_prompt = _build_system_prompt(path)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=tree.model,
        max_turns=1,
        allowed_tools=[],
        include_partial_messages=True,
        env=_sdk_env(),
    )

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_message)
            async for msg in client.receive_response():
                if isinstance(msg, StreamEvent):
                    evt = msg.event
                    if evt.get("type") == "content_block_delta":
                        delta = evt.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield text

                elif isinstance(msg, ResultMessage):
                    break

    except BaseException as exc:
        log.exception("Chat error for node %s", node_id)
        raise
