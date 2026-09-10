"""通知存储(增量 8,spec #14):通知信封落库——按用户扇出,未读 = read_at 为空。

NotificationStore 协议:写路径(record)与读路径(list_for_user / unread_count /
mark_read)均经 create_app / build_supervisor 注入(生产 PostgresNotificationStore,
测试内存替身)——离线快速套件不触库。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from python_backend.db.models import Notification, User
from python_backend.db.session import SessionFactory

# 每组最新条数上限(服务端截断;未读计数不受其影响——徽标可大于面板可见条数)
MAX_PER_KIND = 50


class NotificationStore(Protocol):
    """通知存储协议(增量 8):record 写路径 + 按用户读路径三方法(增量 8-T2)。"""

    async def record(self, notifications: list[dict]) -> None:
        """信封批量落库:对全量现有用户各插一行(read_at 空);重复信封幂等。"""
        ...

    async def list_for_user(self, user_id: int) -> list[dict]:
        """该用户的信封列表(契约五键):每组最近 MAX_PER_KIND 条,整体最新在前。"""
        ...

    async def unread_count(self, user_id: int) -> int:
        """该用户全量未读计数(read_at 空;与每组截断解耦)。"""
        ...

    async def mark_read(self, user_id: int) -> None:
        """该用户全部未读置 read_at=now():天然幂等,重复调用无副作用。"""
        ...


class PostgresNotificationStore(NotificationStore):
    """PG 实现(notifications 表):扇出写 + (user_id, notification_id) 冲突忽略;读路径按用户。"""

    async def record(self, notifications: list[dict]) -> None:
        if not notifications:
            return
        async with SessionFactory() as session:
            user_ids = (await session.execute(select(User.id))).scalars().all()
            if not user_ids:
                return  # 无用户可归属:只广播不落行(管理员 seed 先于业务,实际不可达)
            rows = [
                {
                    "user_id": user_id,
                    "notification_id": payload["notificationId"],
                    "kind": payload["kind"],
                    "message": payload["message"],
                    "order_id": payload["orderId"],
                }
                for user_id in user_ids
                for payload in notifications
            ]
            statement = (
                pg_insert(Notification)
                .values(rows)
                .on_conflict_do_nothing(index_elements=[Notification.user_id, Notification.notification_id])
            )
            await session.execute(statement)
            await session.commit()

    async def list_for_user(self, user_id: int) -> list[dict]:
        """每组窗口排名 ≤ MAX_PER_KIND,整体 created_at + id 倒排(同批时间戳并列时按插入序决胜)。"""
        ranked = (
            select(
                Notification.id,
                Notification.notification_id,
                Notification.kind,
                Notification.message,
                Notification.order_id,
                Notification.created_at,
                func.row_number()
                .over(
                    partition_by=Notification.kind,
                    order_by=(Notification.created_at.desc(), Notification.id.desc()),
                )
                .label("rank_in_kind"),
            )
            .where(Notification.user_id == user_id)
            .subquery()
        )
        async with SessionFactory() as session:
            rows = (
                await session.execute(
                    select(ranked)
                    .where(ranked.c.rank_in_kind <= MAX_PER_KIND)
                    .order_by(ranked.c.created_at.desc(), ranked.c.id.desc())
                )
            ).all()
        return [
            {
                "notificationId": row.notification_id,
                "message": row.message,
                "kind": row.kind,
                "orderId": row.order_id,
                "timestamp": row.created_at.isoformat(),
            }
            for row in rows
        ]

    async def unread_count(self, user_id: int) -> int:
        async with SessionFactory() as session:
            return (
                await session.execute(
                    select(func.count())
                    .select_from(Notification)
                    .where(Notification.user_id == user_id, Notification.read_at.is_(None))
                )
            ).scalar_one()

    async def mark_read(self, user_id: int) -> None:
        async with SessionFactory() as session:
            await session.execute(
                update(Notification)
                .where(Notification.user_id == user_id, Notification.read_at.is_(None))
                .values(read_at=datetime.now(UTC))
            )
            await session.commit()
