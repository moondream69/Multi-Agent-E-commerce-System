"""通知读路径(增量 8-T2,spec #14):内存替身可见语义 + 端点流程(离线,不触 PG)。

替身复现生产语义:按用户隔离、每组最近 50 条(保留最新)、未读 = read_at 空、
mark_read 幂等;端点路由经 create_app 注入替身(认证态 JWT 直签,反证不绕过注入)。
真 PG 读路径语义见 test_notification_store.py(integration)。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from python_backend.api.app import create_app
from python_backend.core.auth import create_token
from python_backend.db.notification_store import MAX_PER_KIND
from tests.conftest import FailingNotificationStore, InMemoryNotificationStore


def _envelope(notification_id: str, *, kind: str = "order_status") -> dict:
    return {
        "notificationId": notification_id,
        "message": f"订单 #1 已发货。({notification_id})",
        "kind": kind,
        "orderId": 1,
        "timestamp": "2026-09-11T00:00:00+00:00",
    }


def _client(store, user_id: int | None) -> TestClient:
    """认证态客户端(JWT 直签,不触登录端点/不触 PG)。"""
    client = TestClient(create_app(auth_required=False, notification_store=store))
    if user_id is not None:
        client.headers.update({"Authorization": f"Bearer {create_token('tester', user_id)}"})
    return client


# —— 内存替身可见语义(与生产同口径) ——


async def test_list_for_user_isolates_users_and_caps_per_kind() -> None:
    """扇出后按用户取回;每组截断保留最新(50)、组间独立;未读计数与截断解耦。"""
    store = InMemoryNotificationStore(user_ids=(1, 2))
    await store.record([_envelope(f"order-{index:02d}") for index in range(MAX_PER_KIND + 5)])
    await store.record([_envelope(f"stock-{index:02d}", kind="inventory_alert") for index in range(3)])

    feed = await store.list_for_user(1)
    kinds = [item["kind"] for item in feed]
    assert kinds.count("order_status") == MAX_PER_KIND, "55 条截断保留 50"
    assert kinds.count("inventory_alert") == 3, "组间截断独立"
    order_ids = [item["notificationId"] for item in feed if item["kind"] == "order_status"]
    assert order_ids[0] == "order-54", "组内最新在前"
    assert "order-05" in order_ids and "order-04" not in order_ids, "截掉的是最旧 5 条"
    assert feed[0]["notificationId"] == "stock-02", "整体最新在前(第二批记录晚于第一批)"

    assert await store.unread_count(1) == MAX_PER_KIND + 5 + 3, "未读为全量计数,不受每组截断影响"


async def test_mark_read_is_idempotent_and_per_user() -> None:
    """mark_read 幂等清零本用户;他人未读不动;已读行仍出现在历史里(只标不删)。"""
    store = InMemoryNotificationStore(user_ids=(1, 2))
    await store.record([_envelope("n-1"), _envelope("n-2", kind="fx_missing")])

    await store.mark_read(1)
    await store.mark_read(1)  # 幂等:重复调用无副作用

    assert await store.unread_count(1) == 0
    assert await store.unread_count(2) == 2, "甲读乙不动"
    assert len(await store.list_for_user(1)) == 2, "已读行仍是历史(读不删除)"


# —— 端点流程(认证态,注入替身) ——


async def test_get_notifications_returns_current_user_feed() -> None:
    """GET 返回 {notifications, unread}:当前用户信封(五键)、未读计数;他人已读不影响本用户。

    响应含注入替身特有的 notificationId → 反证端点经注入分发,未绕过直连 PG。
    """
    store = InMemoryNotificationStore(user_ids=(1, 42))
    await store.record([_envelope("probe-42")])
    await store.mark_read(1)  # 另一用户已读:不应波及 42

    body = _client(store, user_id=42).get("/api/notifications").json()

    assert set(body) == {"notifications", "unread"}
    assert body["unread"] == 1
    assert [item["notificationId"] for item in body["notifications"]] == ["probe-42"]
    assert set(body["notifications"][0]) == {"notificationId", "message", "kind", "orderId", "timestamp"}
    assert body["notifications"][0]["timestamp"] == "2026-09-11T00:00:00+00:00"


async def test_mark_read_endpoint_idempotent_and_user_scoped() -> None:
    """POST read 幂等返回 {unread: 0};仅清当前用户;历史不消失。"""
    store = InMemoryNotificationStore(user_ids=(1, 42))
    await store.record([_envelope("n-read")])
    client = _client(store, user_id=42)

    first = client.post("/api/notifications/read")
    second = client.post("/api/notifications/read")

    assert (first.status_code, first.json()) == (200, {"unread": 0})
    assert second.json() == {"unread": 0}, "重复调用幂等"
    assert client.get("/api/notifications").json()["unread"] == 0
    assert len(client.get("/api/notifications").json()["notifications"]) == 1, "已读行保留在历史"
    assert await store.unread_count(1) == 1, "另一用户未读不受影响"


async def test_unauthenticated_context_skips_store() -> None:
    """未认证(user None)读端点返回空态、标记已读跳过落库(TaskStore 同语义)。

    注入必炸替身:端点若误触存储即 500——200 空态即「零存储调用」的反证。
    """
    client = _client(FailingNotificationStore(), user_id=None)

    assert client.get("/api/notifications").json() == {"notifications": [], "unread": 0}
    assert client.post("/api/notifications/read").json() == {"unread": 0}
