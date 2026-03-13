"""Cross-cutting — Test EventBus.

Tests the EventBus pub/sub mechanism. These test the specific scenarios from the
test plan:
  - emit calls subscriber
  - multiple subscribers
  - off removes subscriber
  - unknown event is noop
  - subscriber error doesn't block others

The EventBus is already implemented in events.py and these tests should pass
against the current implementation.
"""

import asyncio
import logging

import pytest

from events import EventBus


class TestEventBus:

    @pytest.mark.asyncio
    async def test_emit_calls_subscriber(self):
        """bus.on registers a callback; bus.emit invokes it with kwargs."""
        bus = EventBus()
        received = []

        async def handler(**kw):
            received.append(kw)

        bus.on("tree_created", handler)
        await bus.emit("tree_created", tree_id="abc", name="My Tree")
        # Listeners run as tasks, yield to event loop
        await asyncio.sleep(0)

        assert len(received) == 1
        assert received[0]["tree_id"] == "abc"
        assert received[0]["name"] == "My Tree"

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        """Multiple callbacks on the same event all fire."""
        bus = EventBus()
        results = []

        async def cb_a(**kw):
            results.append("a")

        async def cb_b(**kw):
            results.append("b")

        async def cb_c(**kw):
            results.append("c")

        bus.on("update", cb_a)
        bus.on("update", cb_b)
        bus.on("update", cb_c)
        await bus.emit("update")
        await asyncio.sleep(0)

        assert set(results) == {"a", "b", "c"}

    @pytest.mark.asyncio
    async def test_off_removes_subscriber(self):
        """bus.off removes a specific callback; it no longer fires."""
        bus = EventBus()
        calls = []

        async def handler(**kw):
            calls.append(1)

        bus.on("evt", handler)
        bus.off("evt", handler)
        await bus.emit("evt")
        await asyncio.sleep(0)

        assert calls == []

    @pytest.mark.asyncio
    async def test_emit_unknown_event_is_noop(self):
        """Emitting an event with no subscribers does not raise."""
        bus = EventBus()
        # Should not raise
        await bus.emit("nonexistent_event", data=42, info="test")
        # No error = pass

    @pytest.mark.asyncio
    async def test_subscriber_error_does_not_block_others(self):
        """If one subscriber raises, the other subscribers still fire."""
        bus = EventBus()
        results = []

        async def failing_handler(**kw):
            raise RuntimeError("boom")

        async def good_handler(**kw):
            results.append("ok")

        bus.on("evt", failing_handler)
        bus.on("evt", good_handler)

        # Suppress the error log from the failing task
        await bus.emit("evt")
        # Let both tasks run
        await asyncio.sleep(0.05)

        assert "ok" in results
