"""Tests for the EventBus."""

import asyncio
import pytest
from events import EventBus


@pytest.mark.asyncio
async def test_emit_calls_listener():
    """Listener receives kwargs from emit."""
    bus = EventBus()
    received = []

    async def handler(**kw):
        received.append(kw)

    bus.on("test", handler)
    await bus.emit("test", foo="bar", count=1)
    # Listeners run as tasks, need to yield
    await asyncio.sleep(0)
    assert received == [{"foo": "bar", "count": 1}]


@pytest.mark.asyncio
async def test_emit_multiple_listeners():
    """Multiple listeners on the same event all fire."""
    bus = EventBus()
    results = []

    async def h1(**kw):
        results.append("h1")

    async def h2(**kw):
        results.append("h2")

    bus.on("evt", h1)
    bus.on("evt", h2)
    await bus.emit("evt")
    await asyncio.sleep(0)
    assert set(results) == {"h1", "h2"}


@pytest.mark.asyncio
async def test_off_removes_listener():
    """off() removes a specific listener."""
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
async def test_emit_no_listeners():
    """Emitting an event with no listeners does not error."""
    bus = EventBus()
    await bus.emit("nonexistent", data=42)
    # No error = pass


@pytest.mark.asyncio
async def test_emit_different_events_isolated():
    """Listeners only fire for their registered event."""
    bus = EventBus()
    results = []

    async def handler(**kw):
        results.append(kw.get("name"))

    bus.on("a", handler)
    await bus.emit("b", name="wrong")
    await asyncio.sleep(0)
    assert results == []

    await bus.emit("a", name="right")
    await asyncio.sleep(0)
    assert results == ["right"]
