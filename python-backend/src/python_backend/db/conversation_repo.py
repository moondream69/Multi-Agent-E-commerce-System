"""conversations 持久化:每个 customerId 一行,消息数组 JSONB 追加。

写失败只负责抛出,由调用方(api/ws.py)捕获并仅记录日志——审计数据不阻断主流程。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from python_backend.db.models import Conversation
from python_backend.db.session import SessionLocal

logger = logging.getLogger(__name__)


def append_message(
    customer_id: str,
    role: str,
    content: str,
    agent_id: str | None = None,
    task_id: str | None = None,
) -> None:
    with SessionLocal() as session:
        row = session.scalar(select(Conversation).where(Conversation.customerId == customer_id))
        if row is None:
            row = Conversation(customerId=customer_id, agentId=agent_id, messages=[])
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
