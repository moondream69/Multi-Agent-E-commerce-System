"""通知存储 PG 语义(增量 8,spec #14):写路径按用户扇出/幂等/唯一约束;读路径按用户隔离/截断/标记已读。

写路径经 emit_notifications 落库(单元/图级用例已覆盖组装语义);此处验真 PG 行为。
依赖 compose dev Postgres;离线时 TCP 探测秒 skip。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from python_backend.core.auth import hash_password
from python_backend.db.models import Notification, User
from python_backend.db.notification_store import MAX_PER_KIND, PostgresNotificationStore
from python_backend.db.session import SessionFactory

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("requires_postgres")]


async def _make_user() -> int:
    async with SessionFactory() as session, session.begin():
        user = User(username=f"notif-{uuid.uuid4().hex[:8]}", password_hash=hash_password("pw"))
        session.add(user)
        await session.flush()
        return user.id


def _envelope(notification_id: str, *, kind: str = "order_status") -> dict:
    return {
        "notificationId": notification_id,
        "message": "订单 #1 已发货。",
        "kind": kind,
        "orderId": 1,
        "timestamp": "2026-09-11T00:00:00+00:00",
    }


async def test_record_fans_out_to_all_users_idempotently() -> None:
    """扇出:对全量现有用户各一行(read_at 空);重复落库幂等(唯一约束 + on_conflict)。"""
    user_a = await _make_user()
    user_b = await _make_user()
    notification_id = str(uuid.uuid4())
    store = PostgresNotificationStore()

    async with SessionFactory() as session:
        total_users = (await session.execute(select(func.count(User.id)))).scalar_one()

    await store.record([_envelope(notification_id)])
    await store.record([_envelope(notification_id)])  # 重放:不产生重复行

    async with SessionFactory() as session:
        rows = (
            (await session.execute(select(Notification).where(Notification.notification_id == notification_id)))
            .scalars()
            .all()
        )
    assert len(rows) == total_users, "全量现有用户各一行,且重放不增行"
    assert {row.user_id for row in rows} >= {user_a, user_b}
    assert all(row.read_at is None for row in rows), "新落行未读"
    assert rows[0].kind == "order_status"
    assert rows[0].message == "订单 #1 已发货。"


async def test_empty_record_is_noop() -> None:
    """空信封列表零落行(边界):行数不变、不抛错。"""
    async with SessionFactory() as session:
        before = (await session.execute(select(func.count(Notification.id)))).scalar_one()
    await PostgresNotificationStore().record([])
    async with SessionFactory() as session:
        after = (await session.execute(select(func.count(Notification.id)))).scalar_one()
    assert after == before


async def test_unique_constraint_blocks_duplicate_user_notification() -> None:
    """(user_id, notification_id) 唯一约束生效(约束级防线,不依赖 on_conflict 路径)。"""
    user_id = await _make_user()
    notification_id = str(uuid.uuid4())
    async with SessionFactory() as session, session.begin():
        session.add(Notification(user_id=user_id, notification_id=notification_id, kind="order_status", message="m"))
    with pytest.raises(IntegrityError):
        async with SessionFactory() as session, session.begin():
            session.add(
                Notification(user_id=user_id, notification_id=notification_id, kind="order_status", message="m")
            )


# —— 读路径(增量 8-T2):回读信封 / 按用户已读隔离 / 每组截断 ——


async def test_read_path_isolates_users_and_mark_read_is_idempotent() -> None:
    """甲读乙不动;mark_read 幂等;回读为契约信封五键且已读行仍是历史(只标不删)。"""
    user_a = await _make_user()
    user_b = await _make_user()
    notification_id = str(uuid.uuid4())
    store = PostgresNotificationStore()
    await store.record([_envelope(notification_id)])  # 写路径扇出 → 甲、乙各一行

    await store.mark_read(user_a)
    await store.mark_read(user_a)  # 幂等:重复调用无副作用

    assert await store.unread_count(user_a) == 0
    assert await store.unread_count(user_b) == 1, "甲读乙不动"
    feed_a = await store.list_for_user(user_a)
    item = next(row for row in feed_a if row["notificationId"] == notification_id)
    assert set(item) == {"notificationId", "message", "kind", "orderId", "timestamp"}, "契约信封五键"
    assert (item["message"], item["kind"], item["orderId"]) == ("订单 #1 已发货。", "order_status", 1)
    assert item["timestamp"], "时间戳随行落值(created_at)"


async def test_list_for_user_caps_each_kind_keeping_newest() -> None:
    """每组最近 MAX_PER_KIND 条(服务端截断保留最新,组间独立);未读计数与截断解耦。"""
    user_id = await _make_user()
    kind = f"captest-{uuid.uuid4().hex[:8]}"  # 独立 kind 隔离库内他测积累
    notification_ids = [f"cap-{index:03d}" for index in range(MAX_PER_KIND + 5)]
    async with SessionFactory() as session:
        await session.execute(
            pg_insert(Notification).values(
                [
                    {
                        "user_id": user_id,
                        "notification_id": notification_id,
                        "kind": kind,
                        "message": f"m-{notification_id}",
                        "order_id": None,
                    }
                    for notification_id in notification_ids
                ]
            )
        )
        await session.commit()

    feed = [item for item in await PostgresNotificationStore().list_for_user(user_id) if item["kind"] == kind]

    assert [item["notificationId"] for item in feed] == list(reversed(notification_ids))[:MAX_PER_KIND]
    assert await PostgresNotificationStore().unread_count(user_id) == MAX_PER_KIND + 5, "未读全量,不受截断影响"
