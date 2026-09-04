"""agent_tasks 审计持久化:任务开始(in_progress)与结束(completed/failed)写入。

写失败只负责抛出,由调用方(core/orchestrator.py)捕获并仅记录日志——审计不阻断主流程。
"""

from __future__ import annotations

import logging
import uuid

from python_backend.db.models import AgentTask, AgentTaskStatus
from python_backend.db.session import SessionLocal

logger = logging.getLogger(__name__)


def record_task_start(
    task_id: str,
    agent_id: str,
    type_: str,
    input_: dict | None,
    correlation_id: str | None = None,
) -> None:
    with SessionLocal() as session:
        session.add(
            AgentTask(
                id=uuid.UUID(task_id),
                agentId=agent_id,
                type=type_,
                status=AgentTaskStatus.IN_PROGRESS,
                input=input_,
                correlationId=correlation_id,
            )
        )
        session.commit()


def record_task_end(task_id: str, status: AgentTaskStatus, output: dict | None) -> None:
    with SessionLocal() as session:
        row = session.get(AgentTask, uuid.UUID(task_id))
        if row is None:
            logger.warning("任务 %s 审计行不存在,跳过更新", task_id)
            return
        row.status = status
        row.output = output
        session.commit()
