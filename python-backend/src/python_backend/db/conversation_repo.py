"""conversations 持久化:每 (customerId, sessionId) 一行,消息数组 JSONB 追加。

写失败只负责抛出,由调用方(api/ws.py)捕获并仅记录日志——审计数据不阻断主流程。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult

from python_backend.db.models import Conversation
from python_backend.db.session import SessionLocal

logger = logging.getLogger(__name__)

# 旧数据/未指定会话时的回落会话(迁移把现有单行升级为该会话)
DEFAULT_SESSION_ID = "default"
TITLE_MAX_CHARS = 20
SESSION_LIST_LIMIT = 50


def append_message(
    customer_id: str,
    role: str,
    content: str,
    agent_id: str | None = None,
    task_id: str | None = None,
    session_id: str | None = None,
) -> None:
    session_key = session_id or DEFAULT_SESSION_ID
    with SessionLocal() as session:
        row = session.scalar(
            select(Conversation).where(
                Conversation.customerId == customer_id,
                Conversation.sessionId == session_key,
            )
        )
        if row is None:
            row = Conversation(
                customerId=customer_id,
                sessionId=session_key,
                agentId=agent_id,
                messages=[],
                title=content[:TITLE_MAX_CHARS] if role == "user" else None,
            )
            session.add(row)
            session.flush()
        messages = list(row.messages or [])
        entry: dict = {
            "role": role,
            "content": content[:2000],
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if task_id:
            entry["taskId"] = task_id
        messages.append(entry)
        row.messages = messages
        if agent_id and not row.agentId:
            row.agentId = agent_id
        session.commit()


def get_messages(customer_id: str, session_id: str) -> list | None:
    """会话消息数组;会话不存在返回 None。"""
    with SessionLocal() as session:
        row = session.scalar(
            select(Conversation).where(
                Conversation.customerId == customer_id,
                Conversation.sessionId == session_id,
            )
        )
        return list(row.messages or []) if row else None


def list_sessions(customer_id: str, limit: int = SESSION_LIST_LIMIT) -> list[dict]:
    """会话元数据列表:按更新时间倒序,取最近 limit 个。"""
    with SessionLocal() as session:
        rows = session.scalars(
            select(Conversation)
            .where(Conversation.customerId == customer_id)
            .order_by(Conversation.updatedAt.desc(), Conversation.createdAt.desc())
            .limit(limit)
        ).all()
        return [
            {
                "sessionId": row.sessionId,
                "title": row.title or "",
                "updatedAt": row.updatedAt.isoformat() if row.updatedAt else "",
                "messageCount": len(row.messages or []),
            }
            for row in rows
        ]


def delete_session(customer_id: str, session_id: str) -> bool:
    """硬删除会话行;返回是否真的删除了(找不到行返回 False)。"""
    with SessionLocal() as session:
        result = cast(
            CursorResult,
            session.execute(
                delete(Conversation).where(
                    Conversation.customerId == customer_id,
                    Conversation.sessionId == session_id,
                )
            ),
        )
        session.commit()
        return bool(result.rowcount)
