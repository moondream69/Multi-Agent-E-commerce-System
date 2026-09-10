"""会话存储(spec #9 A2 + spec #11):会话列表/删除/重命名 + 挂起审批检查。

conversations 行由会话记忆在首条消息时惰性创建(memory.record,空白会话不落库);
本模块只做驾驶舱会话切换条的读、删与改名。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from python_backend.db.models import ApprovalBatch, ApprovalStatus, Conversation, Task
from python_backend.db.session import SessionFactory


def _conversation_payload(row: Conversation) -> dict:
    """会话序列化(驼峰,与契约 events.ts ConversationMeta 字段一一对应)。"""
    return {
        "sessionId": row.session_id,
        "title": row.title or "",
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        "messageCount": len(row.messages or []),
    }


async def list_conversations(user_id: int) -> list[dict]:
    """当前用户的会话列表(updated_at 倒序):{sessionId, title, updatedAt, messageCount}。"""
    async with SessionFactory() as session:
        rows = (
            (
                await session.execute(
                    select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.updated_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_conversation_payload(row) for row in rows]


async def rename_conversation(user_id: int, session_id: str, title: str) -> dict | None:
    """会话改名;不存在/非本人返回 None(端点 404)。

    自动标题只在建行时写(memory.record),手工命名之后不会被覆盖。
    """
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


async def delete_conversation(user_id: int, session_id: str) -> bool:
    """删除会话行;不存在返回 False(端点映射 404)。任务行保留为审计(列表按会话过滤后不可见)。"""
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


async def session_has_pending_batches(user_id: int, session_id: str) -> bool:
    """该会话所属线程是否仍有挂起审批批次(spec #9:有则拒删,先决定再删)。"""
    async with SessionFactory() as session:
        thread_ids = select(Task.thread_id).where(Task.user_id == user_id, Task.session_id == session_id)
        count = (
            await session.execute(
                select(func.count())
                .select_from(ApprovalBatch)
                .where(ApprovalBatch.thread_id.in_(thread_ids), ApprovalBatch.status == ApprovalStatus.PENDING)
            )
        ).scalar_one()
        return count > 0
