"""会话存储(spec #9 A2 + spec #11):会话列表/删除/重命名 + 挂起审批检查。

conversations 行由会话记忆在首条消息时惰性创建(memory.record,空白会话不落库);
本模块只做驾驶舱会话切换条的读、删与改名。

ConversationStore 协议:端点经 create_app 注入(生产 PostgresConversationStore,测试内存替身)
——离线快速套件不触库(issue #21,与 task_store 同形)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select

from python_backend.core.approvals import ApprovalBatchStore
from python_backend.db.models import Conversation, Task
from python_backend.db.session import SessionFactory


def _conversation_payload(row: Conversation) -> dict:
    """会话序列化(驼峰,与契约 events.ts ConversationMeta 字段一一对应)。"""
    return {
        "sessionId": row.session_id,
        "title": row.title or "",
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        "messageCount": len(row.messages or []),
    }


class ConversationStore(Protocol):
    """会话存储协议(issue #21):四操作与端点可见语义一致。

    挂起审批检查跨「任务行 → 审批批次」两张表,故构造时注批次存储:生产与替身两条
    路径都读同一份批次真源,不各查各的(取代旧模块函数直连 SQL)。
    """

    async def list_conversations(self, user_id: int) -> list[dict]:
        """当前用户的会话列表(updated_at 倒序):{sessionId, title, updatedAt, messageCount}。"""
        ...

    async def rename_conversation(self, user_id: int, session_id: str, title: str) -> dict | None:
        """会话改名并前移其 updated_at;不存在/非本人 None(端点 404)。"""
        ...

    async def delete_conversation(self, user_id: int, session_id: str) -> bool:
        """删除会话行;不存在 False(端点 404);任务行保留为审计。"""
        ...

    async def has_pending_batches(self, user_id: int, session_id: str) -> bool:
        """该会话所属线程是否仍有挂起审批批次(spec #9:有则拒删,先决定再删)。"""
        ...


class PostgresConversationStore(ConversationStore):
    """PG 实现(conversations 表 + 注入的批次存储):issue #21 由模块函数抽为可注入实现,行为零变化。"""

    def __init__(self, batch_store: ApprovalBatchStore) -> None:
        self._batch_store = batch_store

    async def list_conversations(self, user_id: int) -> list[dict]:
        async with SessionFactory() as session:
            rows = (
                (
                    await session.execute(
                        select(Conversation)
                        .where(Conversation.user_id == user_id)
                        .order_by(Conversation.updated_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [_conversation_payload(row) for row in rows]

    async def rename_conversation(self, user_id: int, session_id: str, title: str) -> dict | None:
        """自动标题只在建行时写(memory.record),手工命名之后不会被覆盖。"""
        async with SessionFactory() as session, session.begin():
            row = (
                await session.execute(
                    select(Conversation).where(Conversation.user_id == user_id, Conversation.session_id == session_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            row.title = title
            # 显式写 updated_at:onupdate 是 SQL 侧表达式,flush 后属性过期,读取会触发同步惰性加载(MissingGreenlet)
            row.updated_at = datetime.now(UTC)
            await session.flush()
            return _conversation_payload(row)

    async def delete_conversation(self, user_id: int, session_id: str) -> bool:
        async with SessionFactory() as session, session.begin():
            row = (
                await session.execute(
                    select(Conversation).where(Conversation.user_id == user_id, Conversation.session_id == session_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            return True

    async def has_pending_batches(self, user_id: int, session_id: str) -> bool:
        async with SessionFactory() as session:
            thread_ids = (
                (
                    await session.execute(
                        select(Task.thread_id).where(Task.user_id == user_id, Task.session_id == session_id)
                    )
                )
                .scalars()
                .all()
            )
        # 会话任务数最多几十(切片任务),逐线程查注入的批次存储即可;真源单一,勿另写一份 status 判定
        for thread_id in thread_ids:
            if await self._batch_store.list_pending(thread_id):
                return True
        return False
