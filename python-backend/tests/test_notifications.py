"""通知组装单测(spec #9 A8/A9/A14):文案映射 / 五档复用 / kind 归组 / id 注入(纯函数,无 DB)。"""

from __future__ import annotations

import pytest

from python_backend.core.notifications import (
    KIND_FX_MISSING,
    KIND_INVENTORY_ALERT,
    KIND_ORDER_STATUS,
    ORDER_STATUS_MESSAGES,
    build_notifications,
    inventory_alert_message,
)
from tests.conftest import FailingNotificationStore, InMemoryNotificationStore, RecordingEmitter


def _ids():
    counter = iter(f"n-{i}" for i in range(100))
    return lambda: next(counter)


def test_order_status_messages_cover_seven_states() -> None:
    """A8:映射表覆盖订单七态(继承场景,文案零 LLM)。"""
    assert set(ORDER_STATUS_MESSAGES) == {
        "pending",
        "confirmed",
        "processing",
        "shipped",
        "delivered",
        "cancelled",
        "returned",
    }
    assert "#5" in ORDER_STATUS_MESSAGES["shipped"].format(order_id=5)


def test_order_status_messages_seller_voice() -> None:
    """#18(A8 文案):七条为卖家运营口吻——无「您」买家视角,逐条钉住。"""
    pinned = {
        "pending": "新订单 #{order_id} 已提交,待确认。",
        "confirmed": "订单 #{order_id} 已确认。",
        "processing": "订单 #{order_id} 进入处理中。",
        "shipped": "订单 #{order_id} 已发货。",
        "delivered": "订单 #{order_id} 已送达。",
        "cancelled": "订单 #{order_id} 已取消。",
        "returned": "订单 #{order_id} 已完成退货。",
    }
    assert pinned == ORDER_STATUS_MESSAGES
    assert all("您" not in text for text in ORDER_STATUS_MESSAGES.values())


def test_build_order_status_notification() -> None:
    """订单状态效果 → 通知信封(order_status,携 orderId)。"""
    effects = [{"type": "order_status", "order_id": 7, "from": "pending", "to": "confirmed"}]
    notifications = build_notifications(effects, new_id=_ids())
    assert len(notifications) == 1
    assert notifications[0]["notificationId"] == "n-0"
    assert notifications[0]["message"] == "订单 #7 已确认。"
    assert notifications[0]["kind"] == KIND_ORDER_STATUS
    assert notifications[0]["orderId"] == 7
    assert notifications[0]["timestamp"]


def test_build_ignores_unknown_status_and_unknown_effect() -> None:
    """未知状态/未知效果类型静默忽略(防御:状态机外的值不产生误导通知)。"""
    effects = [
        {"type": "order_status", "order_id": 7, "to": "archived"},
        {"type": "no_such_effect"},
    ]
    assert build_notifications(effects, new_id=_ids()) == []


def test_inventory_five_tier_messages() -> None:
    """A9:五档文案(售罄/严重不足/偏低/接近安全线/充足),与 check_inventory 共用。"""
    assert "已售罄" in inventory_alert_message("蓝牙音箱", 0, 10)
    assert "严重不足" in inventory_alert_message("蓝牙音箱", 2, 10)
    assert "偏低" in inventory_alert_message("蓝牙音箱", 5, 10)
    assert "接近安全线" in inventory_alert_message("蓝牙音箱", 9, 10)
    assert "充足" in inventory_alert_message("蓝牙音箱", 10, 10)


def test_build_inventory_notification() -> None:
    """库存告警效果 → 通知信封(inventory_alert,无 orderId)。"""
    effects = [{"type": "inventory_low", "product_id": 3, "title": "蓝牙音箱", "stock": 1, "threshold": 10}]
    notification = build_notifications(effects, new_id=_ids())[0]
    assert notification["kind"] == KIND_INVENTORY_ALERT
    assert "严重不足" in notification["message"]
    assert notification["orderId"] is None


def test_build_fx_missing_notification() -> None:
    """B10 演进:汇率缺失效果 → fx_missing 通知(人工可见待核)。"""
    notification = build_notifications([{"type": "fx_missing", "order_id": 9}], new_id=_ids())[0]
    assert notification["kind"] == KIND_FX_MISSING
    assert "#9" in notification["message"]
    assert notification["orderId"] == 9


async def test_emit_notifications_records_then_broadcasts() -> None:
    """组装点扩展(增量 8-T1):每个通知落库(按用户扇出)+ 广播 notification.created。"""
    from python_backend.core.notifications import emit_notifications

    store = InMemoryNotificationStore()
    emitter = RecordingEmitter()

    await emit_notifications(
        store,
        emitter,
        [
            {"type": "order_status", "order_id": 1, "to": "shipped"},
            {"type": "fx_missing", "order_id": 1},
        ],
    )
    assert emitter.names() == ["notification.created", "notification.created"]
    assert {payload["kind"] for _event, payload in emitter.events} == {KIND_ORDER_STATUS, KIND_FX_MISSING}
    assert len(store.rows) == 2, "默认扇出到 1 个用户:两个信封各一行"
    assert {row["kind"] for row in store.rows} == {KIND_ORDER_STATUS, KIND_FX_MISSING}
    assert [row["notificationId"] for row in store.rows] == [
        payload["notificationId"] for _event, payload in emitter.events
    ], "落库信封与广播载荷同源"


async def test_emit_notifications_store_failure_still_broadcasts() -> None:
    """落库失败按辅助簿记分类(增量 8-T1):仅日志,不阻塞广播(实时投递尽力而为)。"""
    from python_backend.core.notifications import emit_notifications

    emitter = RecordingEmitter()
    await emit_notifications(
        FailingNotificationStore(), emitter, [{"type": "order_status", "order_id": 1, "to": "shipped"}]
    )
    assert emitter.names() == ["notification.created"]


@pytest.mark.parametrize("effects", [[], [{"type": "order_status", "order_id": 1, "to": "pending"}]])
async def test_emit_notifications_empty_or_single(effects: list[dict]) -> None:
    """空效果零存储零发射;单效果各一次(边界)。"""
    from python_backend.core.notifications import emit_notifications

    store = InMemoryNotificationStore()
    emitter = RecordingEmitter()
    await emit_notifications(store, emitter, effects)
    assert len(emitter.events) == len(effects)
    assert len(store.rows) == len(effects)
