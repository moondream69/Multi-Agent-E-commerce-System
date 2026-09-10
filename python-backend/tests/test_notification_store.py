"""通知存储 PG 语义(增量 8-T1,spec #14):按用户扇出、重复落库幂等、唯一约束。

写路径经 emit_notifications 落库(单元/图级用例已覆盖组装语义);此处验真 PG 行为。
依赖 compose dev Postgres;离线时 TCP 探测秒 skip。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from python_backend.core.auth import hash_password
from python_backend.db.models import Notification, User
from python_backend.db.notification_store import PostgresNotificationStore
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
