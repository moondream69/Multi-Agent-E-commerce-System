"""通知存储(增量 8-T1,spec #14):通知信封落库——按用户扇出,未读 = read_at 为空。

NotificationStore 协议:写路径经 create_app / build_supervisor 注入
(生产 PostgresNotificationStore,测试内存替身)——离线快速套件不触库。
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from python_backend.db.models import Notification, User
from python_backend.db.session import SessionFactory


class NotificationStore(Protocol):
    """通知存储协议(增量 8-T1):record 之外的操作随读路径增量补齐。"""

    async def record(self, notifications: list[dict]) -> None:
        """信封批量落库:对全量现有用户各插一行(read_at 空);重复信封幂等。"""
        ...


class PostgresNotificationStore(NotificationStore):
    """PG 实现(notifications 表):扇出写 + (user_id, notification_id) 冲突忽略。"""

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
