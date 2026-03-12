"""Tests for the cancel mechanism — specifically the timeout-based queue consumer."""

import asyncio
import pytest


async def test_queue_consumer_breaks_on_cancel_flag():
    """Simulates _run_chat's queue consumer loop: a hung queue + cancel flag
    should cause the loop to break within the timeout window."""
    cancelled = set()
    event_queue: asyncio.Queue = asyncio.Queue()
    nid = "test-node"
    events_received = []

    async def consumer():
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if nid in cancelled:
                    break
                continue
            if event is None:
                break
            events_received.append(event)

    # Start the consumer
    task = asyncio.create_task(consumer())

    # Send one event, then mark as cancelled (simulating a hung SDK)
    await event_queue.put("event1")
    await asyncio.sleep(0.1)
    cancelled.add(nid)

    # Consumer should exit within ~0.5s (the timeout)
    await asyncio.wait_for(task, timeout=2.0)
    assert events_received == ["event1"]


async def test_queue_consumer_exits_on_none():
    """Normal completion: generator puts None when done."""
    event_queue: asyncio.Queue = asyncio.Queue()
    cancelled = set()
    nid = "test-node"
    events_received = []

    async def consumer():
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if nid in cancelled:
                    break
                continue
            if event is None:
                break
            events_received.append(event)

    task = asyncio.create_task(consumer())

    await event_queue.put("e1")
    await event_queue.put("e2")
    await event_queue.put(None)

    await asyncio.wait_for(task, timeout=2.0)
    assert events_received == ["e1", "e2"]


async def test_queue_consumer_continues_on_timeout_without_cancel():
    """If not cancelled, timeout should not break the loop — it should keep waiting."""
    event_queue: asyncio.Queue = asyncio.Queue()
    cancelled = set()
    nid = "test-node"
    events_received = []

    async def consumer():
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.3)
            except asyncio.TimeoutError:
                if nid in cancelled:
                    break
                continue
            if event is None:
                break
            events_received.append(event)

    task = asyncio.create_task(consumer())

    # Wait longer than the timeout, then send an event
    await asyncio.sleep(0.5)
    await event_queue.put("late-event")
    await event_queue.put(None)

    await asyncio.wait_for(task, timeout=2.0)
    assert events_received == ["late-event"]
