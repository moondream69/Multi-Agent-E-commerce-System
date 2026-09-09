"""会话存储(spec #9 A2):会话列表/删除 + 挂起审批检查。

conversations 行由会话记忆在首条消息时惰性创建(memory.record,空白会话不落库);
本模块只做驾驶舱会话切换条的读与删。
"""

from __future__ import annotations

from sqlalchemy import func, select

from python_backend.db.models import ApprovalBatch, ApprovalStatus, Conversation, Task
from python_backend.db.session import SessionFactory


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
        return [
            {
                "sessionId": row.session_id,
                "title": row.title or "",
                "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
                "messageCount": len(row.messages or []),
            }
            for row in rows
        ]


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
