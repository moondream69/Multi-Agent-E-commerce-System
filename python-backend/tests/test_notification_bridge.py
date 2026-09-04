"""chat:notification 桥接测试:customer.notification → 买家视角通知信封(纯单测,无 DB)。"""

from __future__ import annotations

import asyncio

from python_backend.api.ws import bridge_notifications
from python_backend.core.event_bus import EventBus
from python_backend.domain.events import AgentEventType


class FakeSio:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    async def emit(self, event: str, data: dict) -> None:
        self.emitted.append((event, data))


async def test_bridge_emits_notification_envelope():
    bus = EventBus()
    sio = FakeSio()
    bridge_notifications(sio, bus)

    bus.emit(
        AgentEventType.CUSTOMER_NOTIFICATION,
        {"message": "您的订单已发货", "agentId": "customer-service", "orderId": "ord-1"},
    )
    # EventBus.emit 对 async handler 用 create_task 调度,让出事件循环等待执行
    await asyncio.sleep(0.05)

    assert len(sio.emitted) == 1
    event, data = sio.emitted[0]
    assert event == "chat:notification"
    assert data["type"] == "chat:notification"
    assert data["message"] == "您的订单已发货"
    assert data["agentId"] == "customer-service"
    assert data["orderId"] == "ord-1"
    assert data["notificationId"]
    assert data["timestamp"]


async def test_bridge_uses_defaults_for_missing_fields():
    bus = EventBus()
    sio = FakeSio()
    bridge_notifications(sio, bus)

    bus.emit(AgentEventType.CUSTOMER_NOTIFICATION, {"message": "库存告警"})
    await asyncio.sleep(0.05)

    _, data = sio.emitted[0]
    assert data["agentId"] == "customer-service"
    assert data["orderId"] is None


async def test_bridge_ignores_other_events():
    bus = EventBus()
    sio = FakeSio()
    bridge_notifications(sio, bus)

    bus.emit(AgentEventType.REPORT_GENERATED, {"title": "x"})
    await asyncio.sleep(0.05)
    assert len(sio.emitted) == 0
