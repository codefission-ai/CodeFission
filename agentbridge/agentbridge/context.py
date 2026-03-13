"""Cross-provider context transfer.

When switching from one provider to another mid-session, we extract a text
summary of the conversation history and inject it as a preamble to the prompt
for the new provider. This is inherently lossy — tool call details, raw JSON,
and provider-specific metadata are simplified — but it preserves the
conversational context well enough for the new agent to continue.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Message:
    """A single message in a conversation."""
    role: str       # "assistant", "user", "tool"
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    is_error: bool = False


@dataclass
class ConversationHistory:
    """Accumulated history from a provider session."""
    provider: str
    session_id: str
    messages: list[Message] = field(default_factory=list)


def extract_history(events: list[dict]) -> ConversationHistory:
    """Build a ConversationHistory from a list of serialized BridgeEvents.

    Each dict should have at minimum: kind, provider, and kind-specific fields.
    This works with events that have been serialized via dataclasses.asdict().
    """
    history = ConversationHistory(provider="", session_id="")
    text_buffer: list[str] = []

    for evt in events:
        kind = evt.get("kind", "")

        if kind == "session_init":
            history.provider = evt.get("provider", "")
            history.session_id = evt.get("session_id", "")

        elif kind == "text_delta":
            text_buffer.append(evt.get("text", ""))

        elif kind == "tool_start":
            # Flush any accumulated text
            if text_buffer:
                history.messages.append(Message(
                    role="assistant",
                    content="".join(text_buffer),
                ))
                text_buffer = []
            history.messages.append(Message(
                role="tool",
                content=f"Called {evt.get('name', 'unknown')}",
                tool_name=evt.get("name"),
                tool_call_id=evt.get("tool_call_id"),
            ))

        elif kind == "tool_end":
            result = evt.get("result", "")
            # Truncate long results
            if len(result) > 500:
                result = result[:500] + "..."
            history.messages.append(Message(
                role="tool",
                content=result,
                tool_name=evt.get("name"),
                tool_call_id=evt.get("tool_call_id"),
                is_error=evt.get("is_error", False),
            ))

        elif kind == "turn_complete":
            # Flush remaining text
            if text_buffer:
                history.messages.append(Message(
                    role="assistant",
                    content="".join(text_buffer),
                ))
                text_buffer = []

    # Flush any trailing text
    if text_buffer:
        history.messages.append(Message(
            role="assistant",
            content="".join(text_buffer),
        ))

    return history


def format_history_as_context(history: ConversationHistory) -> str:
    """Format a ConversationHistory into a text block for injection.

    Returns a human-readable summary that can be prepended to a prompt
    when switching providers.
    """
    if not history.messages:
        return ""

    lines = [
        f"[Context from previous {history.provider} session {history.session_id}]",
        "",
    ]

    for msg in history.messages:
        if msg.role == "assistant":
            lines.append(f"Assistant: {msg.content}")
        elif msg.role == "user":
            lines.append(f"User: {msg.content}")
        elif msg.role == "tool":
            if msg.tool_name and msg.content.startswith("Called "):
                lines.append(f"[Tool: {msg.tool_name}]")
            else:
                prefix = "ERROR" if msg.is_error else "Result"
                lines.append(f"[{prefix}: {msg.content}]")
        lines.append("")

    lines.append("[End of previous context]")
    return "\n".join(lines)


def build_context_prompt(
    history: ConversationHistory,
    new_prompt: str,
) -> str:
    """Combine prior context with a new prompt for cross-provider transfer.

    Returns the full prompt string with context prepended.
    """
    context = format_history_as_context(history)
    if not context:
        return new_prompt
    return context + "\n\n" + new_prompt
