"""OpenAI provider adapter with streaming and tool call support.

Copied from WhatTheBot's core/providers/openai_provider.py.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from .base import ProviderBase, ProviderMessage, StreamEvent, TokenUsage, ToolCall

MODELS = {
    "gpt-4o": {"context": 128_000, "input": 2.50e-6, "output": 10.0e-6},
    "gpt-4o-2024-08-06": {"context": 128_000, "input": 2.50e-6, "output": 10.0e-6},
    "gpt-4o-mini": {"context": 128_000, "input": 0.15e-6, "output": 0.60e-6},
    "gpt-4.1": {"context": 1_047_576, "input": 2.00e-6, "output": 8.00e-6},
    "gpt-4.1-mini": {"context": 1_047_576, "input": 0.40e-6, "output": 1.60e-6},
    "gpt-4.1-nano": {"context": 1_047_576, "input": 0.10e-6, "output": 0.40e-6},
    "o3-mini": {"context": 200_000, "input": 1.10e-6, "output": 4.40e-6},
}

DEFAULT_CONTEXT = 128_000


class OpenAIProvider(ProviderBase):
    def __init__(self, model: str) -> None:
        self.model = model
        self._client = AsyncOpenAI()
        self._model_info = MODELS.get(model, {})

    async def stream(
        self, messages: list[ProviderMessage], tools: list[dict] | None = None
    ) -> AsyncIterator[StreamEvent]:
        api_msgs = self._to_api_messages(messages)
        kwargs: dict = dict(
            model=self.model,
            messages=api_msgs,
            stream=True,
            stream_options={"include_usage": True},
        )
        if tools:
            kwargs["tools"] = tools

        response = await self._client.chat.completions.create(**kwargs)

        # Buffer for assembling tool call deltas
        tool_buffers: dict[int, dict] = {}  # index -> {id, name, arguments_str}

        async for chunk in response:
            # Usage info in final chunk
            if chunk.usage:
                yield StreamEvent(
                    type="usage",
                    usage=TokenUsage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                    ),
                )

            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            # Text content
            if delta.content:
                yield StreamEvent(type="text_delta", text=delta.content)

            # Tool call deltas
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_buffers:
                        tool_buffers[idx] = {
                            "id": tc_delta.id or "",
                            "name": tc_delta.function.name or "" if tc_delta.function else "",
                            "arguments": "",
                        }
                        if tool_buffers[idx]["name"]:
                            yield StreamEvent(
                                type="tool_call_start",
                                tool_name=tool_buffers[idx]["name"],
                            )
                    else:
                        if tc_delta.id:
                            tool_buffers[idx]["id"] = tc_delta.id
                        if tc_delta.function and tc_delta.function.name:
                            tool_buffers[idx]["name"] = tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        tool_buffers[idx]["arguments"] += tc_delta.function.arguments

            # Finish reason: emit completed tool calls
            if choice.finish_reason:
                for idx in sorted(tool_buffers):
                    buf = tool_buffers[idx]
                    try:
                        args = json.loads(buf["arguments"]) if buf["arguments"] else {}
                    except json.JSONDecodeError:
                        args = {"_raw": buf["arguments"]}
                    yield StreamEvent(
                        type="tool_call_end",
                        tool_call=ToolCall(
                            id=buf["id"], name=buf["name"], arguments=args
                        ),
                    )
                tool_buffers.clear()

        yield StreamEvent(type="done")

    def format_tools(self, tool_defs: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": td["name"],
                    "description": td["description"],
                    "parameters": td["parameters"],
                },
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

    def _to_api_messages(self, messages: list[ProviderMessage]) -> list[dict]:
        result = []
        for msg in messages:
            if msg.role == "tool_result":
                result.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                })
            elif msg.role == "assistant" and msg.tool_calls:
                result.append({
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })
            else:
                result.append({"role": msg.role, "content": msg.content})
        return result
