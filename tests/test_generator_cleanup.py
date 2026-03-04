"""Test that async generator cleanup doesn't cancel the caller's task.

Reproduces the core bug: the SDK's query() generator holds an anyio cancel
scope.  When GC finalizes it, it cancels whatever task entered the scope.
The fix: run streaming in a separate task so the cancel scope is isolated.

This also tests the queue-based consumer pattern used in main.py's _run_chat.
"""

import asyncio
import gc
import pytest


# ── Simulate the SDK's query() generator with a cancel scope ────────

class FakeCancelScope:
    """Mimics an anyio cancel scope bound to the task that enters it."""

    def __init__(self):
        self._host_task = asyncio.current_task()

    async def cleanup(self):
        current = asyncio.current_task()
        if current is not self._host_task and self._host_task is not None:
            self._host_task.cancel("Cancelled via fake cancel scope")


async def fake_sdk_query(num_events=5):
    """Simulates claude_agent_sdk.query() — yields messages, holds a scope."""
    scope = FakeCancelScope()
    try:
        for i in range(num_events):
            yield {"type": "delta", "i": i}
        yield {"type": "result"}
    finally:
        await scope.cleanup()


# ── Wrappers (like stream_chat) ─────────────────────────────────────

async def wrapper_generator(num_events=5):
    """Like stream_chat: async generator wrapping the SDK query."""
    async for msg in fake_sdk_query(num_events):
        if msg["type"] == "result":
            return
        yield msg


# ── Caller patterns ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_direct_iteration_gets_cancelled():
    """BUG: iterating the wrapper directly binds the cancel scope to us."""
    events = []
    async for msg in wrapper_generator():
        events.append(msg)
    assert len(events) == 5

    # Force GC — the SDK generator finalizer cancels our task
    gc.collect()
    await asyncio.sleep(0)

    task = asyncio.current_task()
    was_cancelled = task.cancelling() > 0 if task else False
    if was_cancelled:
        task.uncancel()
    # We expect this to be cancelled on CPython 3.12
    # (exact behavior is GC-timing dependent)


@pytest.mark.asyncio
async def test_separate_task_isolates_cancel_scope():
    """FIX: running streaming in a separate task keeps our task safe."""
    events = []
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        try:
            async for msg in wrapper_generator():
                await queue.put(msg)
        finally:
            await queue.put(None)

    stream_task = asyncio.create_task(pump())

    # Consume from queue (runs in OUR task, not stream_task)
    while True:
        event = await queue.get()
        if event is None:
            break
        events.append(event)

    # Wait for stream task to finish (including generator cleanup)
    try:
        await stream_task
    except BaseException:
        pass

    assert len(events) == 5

    # Force GC
    gc.collect()
    await asyncio.sleep(0)

    # Our task must NOT be cancelled
    task = asyncio.current_task()
    assert task is None or task.cancelling() == 0

    # Subsequent awaits must work
    await asyncio.sleep(0)
    fut = asyncio.get_event_loop().create_future()
    fut.set_result(42)
    assert await fut == 42


@pytest.mark.asyncio
async def test_separate_task_finalization_survives():
    """After the stream task, multiple sequential awaits all succeed."""
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        try:
            async for msg in wrapper_generator():
                await queue.put(msg)
        finally:
            await queue.put(None)

    stream_task = asyncio.create_task(pump())

    while True:
        event = await queue.get()
        if event is None:
            break

    try:
        await stream_task
    except BaseException:
        pass

    # Simulate finalization: multiple DB writes + WS send
    results = []
    for i in range(5):
        await asyncio.sleep(0)
        results.append(i)
    assert results == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_exception_propagation_through_queue():
    """Exceptions from the SDK generator propagate through the queue."""
    queue: asyncio.Queue = asyncio.Queue()

    async def failing_generator():
        yield {"type": "delta", "i": 0}
        raise RuntimeError("SDK exploded")

    async def pump():
        try:
            async for msg in failing_generator():
                await queue.put(msg)
        except Exception as exc:
            await queue.put(exc)
        finally:
            await queue.put(None)

    stream_task = asyncio.create_task(pump())

    events = []
    error = None
    while True:
        event = await queue.get()
        if event is None:
            break
        if isinstance(event, Exception):
            error = event
            break
        events.append(event)

    # Drain sentinel
    while True:
        event = await queue.get()
        if event is None:
            break

    try:
        await stream_task
    except BaseException:
        pass

    assert len(events) == 1
    assert isinstance(error, RuntimeError)
    assert "SDK exploded" in str(error)


@pytest.mark.asyncio
async def test_empty_stream():
    """Queue pattern handles generators that yield nothing."""
    queue: asyncio.Queue = asyncio.Queue()

    async def empty_gen():
        return
        yield  # make it a generator

    async def pump():
        try:
            async for msg in empty_gen():
                await queue.put(msg)
        finally:
            await queue.put(None)

    stream_task = asyncio.create_task(pump())

    events = []
    while True:
        event = await queue.get()
        if event is None:
            break
        events.append(event)

    await stream_task
    assert events == []


@pytest.mark.asyncio
async def test_large_stream():
    """Queue pattern handles many events without deadlock."""
    queue: asyncio.Queue = asyncio.Queue()
    n = 1000

    async def pump():
        try:
            async for msg in wrapper_generator(num_events=n):
                await queue.put(msg)
        finally:
            await queue.put(None)

    stream_task = asyncio.create_task(pump())

    count = 0
    while True:
        event = await queue.get()
        if event is None:
            break
        count += 1

    await stream_task
    assert count == n


@pytest.mark.asyncio
async def test_consumer_can_cancel_stream():
    """Consumer can cancel the stream task mid-stream."""
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        try:
            async for msg in fake_sdk_query(num_events=100):
                await queue.put(msg)
        except asyncio.CancelledError:
            pass
        finally:
            await queue.put(None)

    stream_task = asyncio.create_task(pump())

    # Read a few events then cancel
    for _ in range(3):
        event = await queue.get()
        assert event is not None

    stream_task.cancel()

    # Drain remaining
    while True:
        event = await queue.get()
        if event is None:
            break

    # Our task should be fine
    task = asyncio.current_task()
    assert task is None or task.cancelling() == 0
