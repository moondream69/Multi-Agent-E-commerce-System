"""事件总线(1:1 迁移 event-bus.service.spec.ts)。"""

import asyncio

import pytest

from python_backend.core.event_bus import EventBus
from python_backend.domain.events import AgentEventType


@pytest.fixture()
def bus() -> EventBus:
    return EventBus()


async def test_emit_notifies_subscriber(bus):
    received = asyncio.Event()
    payloads: list = []

    def handler(event):
        payloads.append(event.payload)
        received.set()

    bus.on(AgentEventType.TASK_ASSIGNED, handler)
    bus.emit(AgentEventType.TASK_ASSIGNED, {"data": "hello"}, "corr-1")

    await asyncio.wait_for(received.wait(), timeout=1)
    assert payloads == [{"data": "hello"}]


async def test_supports_multiple_subscribers(bus):
    count = 0
    done = asyncio.Event()

    def handler(_event):
        nonlocal count
        count += 1
        if count >= 2:
            done.set()

    bus.on(AgentEventType.PRODUCT_CREATED, handler)
    bus.on(AgentEventType.PRODUCT_CREATED, handler)
    bus.emit(AgentEventType.PRODUCT_CREATED, {})

    await asyncio.wait_for(done.wait(), timeout=1)
    assert count == 2


async def test_broadcast_notifies_all_handlers(bus):
    results: list[str] = []

    def h1(event):
        results.append("h1:" + event.payload["data"])

    def h2(event):
        results.append("h2:" + event.payload["data"])

    await bus.broadcast(AgentEventType.REPORT_GENERATED, {"data": "test"}, [h1, h2])

    assert set(results) == {"h1:test", "h2:test"}
    assert len(results) == 2
