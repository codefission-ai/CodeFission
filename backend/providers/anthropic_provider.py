"""Anthropic provider adapter with streaming and tool call support.

Copied from WhatTheBot's core/providers/anthropic_provider.py.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from .base import ProviderBase, ProviderMessage, StreamEvent, TokenUsage, ToolCall

MODELS = {
    "claude-sonnet-4-5-20250929": {"context": 200_000, "input": 3.00e-6, "output": 15.0e-6},
    "claude-sonnet-4-6": {"context": 200_000, "input": 3.00e-6, "output": 15.0e-6},
    "claude-opus-4-6": {"context": 200_000, "input": 15.0e-6, "output": 75.0e-6},
    "claude-haiku-4-5-20251001": {"context": 200_000, "input": 0.80e-6, "output": 4.00e-6},
}

DEFAULT_CONTEXT = 200_000
MAX_TOKENS = 4096


class AnthropicProvider(ProviderBase):
    def __init__(self, model: str) -> None:
        self.model = model
        self._client = AsyncAnthropic()
        self._model_info = MODELS.get(model, {})

    async def stream(
        self, messages: list[ProviderMessage], tools: list[dict] | None = None
    ) -> AsyncIterator[StreamEvent]:
        system_text, api_msgs = self._to_api_messages(messages)
        kwargs: dict = dict(
            model=self.model,
            max_tokens=MAX_TOKENS,
            messages=api_msgs,
        )
        if system_text:
            kwargs["system"] = system_text
        if tools:
            kwargs["tools"] = tools

        async with self._client.messages.stream(**kwargs) as stream:
            current_tool: dict | None = None

            async for event in stream:
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool = {
                            "id": block.id,
                            "name": block.name,
                            "arguments": "",
                        }
                        yield StreamEvent(
                            type="tool_call_start", tool_name=block.name
                        )

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield StreamEvent(type="text_delta", text=delta.text)
                    elif delta.type == "input_json_delta":
                        if current_tool is not None:
                            current_tool["arguments"] += delta.partial_json

                elif event.type == "content_block_stop":
                    if current_tool is not None:
                        try:
                            args = (
                                json.loads(current_tool["arguments"])
                                if current_tool["arguments"]
                                else {}
                            )
                        except json.JSONDecodeError:
                            args = {"_raw": current_tool["arguments"]}
                        yield StreamEvent(
                            type="tool_call_end",
                            tool_call=ToolCall(
                                id=current_tool["id"],
                                name=current_tool["name"],
                                arguments=args,
                            ),
                        )
                        current_tool = None

                elif event.type == "message_delta":
                    usage = getattr(event, "usage", None)
                    if usage:
                        yield StreamEvent(
                            type="usage",
                            usage=TokenUsage(
                                input_tokens=0,
                                output_tokens=usage.output_tokens,
                            ),
                        )

                elif event.type == "message_start":
                    msg = event.message
                    if hasattr(msg, "usage") and msg.usage:
                        yield StreamEvent(
                            type="usage",
                            usage=TokenUsage(
                                input_tokens=msg.usage.input_tokens,
                                output_tokens=0,
                            ),
                        )

        yield StreamEvent(type="done")

    def format_tools(self, tool_defs: list[dict]) -> list[dict]:
        return [
            {
                "name": td["name"],
                "description": td["description"],
                "input_schema": td["parameters"],
            }
            for td in tool_defs
        ]

    def format_tool_result(
        self, tool_call_id: str, tool_name: str, result: str, is_error: bool
    ) -> ProviderMessage:
        return ProviderMessage(
            role="tool_result",
            content=result,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )

    @property
    def context_window(self) -> int:
        return self._model_info.get("context", DEFAULT_CONTEXT)

    def cost_per_token(self) -> tuple[float, float]:
        info = self._model_info
        return (info.get("input", 0.0), info.get("output", 0.0))

    def _to_api_messages(
        self, messages: list[ProviderMessage]
    ) -> tuple[str, list[dict]]:
        """Convert to Anthropic format. Returns (system_text, messages)."""
        system_text = ""
        api_msgs: list[dict] = []

        for msg in messages:
            if msg.role == "system":
                system_text = msg.content
                continue

            if msg.role == "tool_result":
                # Anthropic requires tool_result inside a user message
                block = {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id,
                    "content": msg.content,
                }
                # Merge into previous user message if it exists and has tool_result content
                if (
                    api_msgs
                    and api_msgs[-1]["role"] == "user"
                    and isinstance(api_msgs[-1]["content"], list)
                ):
                    api_msgs[-1]["content"].append(block)
                else:
                    api_msgs.append({"role": "user", "content": [block]})
                continue

            if msg.role == "assistant" and msg.tool_calls:
                content: list[dict] = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                api_msgs.append({"role": "assistant", "content": content})
                continue

            # Regular user or assistant message
            api_msgs.append({"role": msg.role, "content": msg.content})

        return system_text, api_msgs
