"""AgentTask 审计(spec #8 遗留):切片执行与审批决定两类事件落库审计源。

- AuditWriter 协议:图节点与 REST 依赖(测试注入记录器,默认 no-op——与 emitter/tracer 同接缝风格)
- PgAuditWriter:agent_tasks 表落库;correlation_id 与 Langfuse 任务 trace 互链
  (task_trace_id 确定性派生,同一任务全部审计行指向同一 trace)
"""

from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy import select

from python_backend.db.models import AgentTask, Task
from python_backend.db.session import SessionFactory
from python_backend.infrastructure.tracing import task_trace_id

logger = logging.getLogger(__name__)


class AuditWriter(Protocol):
    """审计写入协议:record 一条 AgentTask 事件(测试注入记录器)。"""

    async def record(
        self,
        *,
        thread_id: str,
        agent_id: str,
        type_: str,
        status: str,
        input: dict | None = None,
        output: dict | None = None,
    ) -> None: ...


class NullAuditWriter:
    """未装配审计时的 no-op(单测/简化装配)。"""

    async def record(
        self,
        *,
        thread_id: str,
        agent_id: str,
        type_: str,
        status: str,
        input: dict | None = None,
        output: dict | None = None,
    ) -> None:
        return None


class PgAuditWriter:
    """agent_tasks 表实现:task_id 按 thread 反查(无任务行则空),correlation_id = 任务 trace_id。"""

    async def record(
        self,
        *,
        thread_id: str,
        agent_id: str,
        type_: str,
        status: str,
        input: dict | None = None,
        output: dict | None = None,
    ) -> None:
        try:
            async with SessionFactory() as session, session.begin():
                task_id = (
                    await session.execute(select(Task.id).where(Task.thread_id == thread_id))
                ).scalar_one_or_none()
                session.add(
                    AgentTask(
                        task_id=task_id,
                        agent_id=agent_id,
                        type=type_,
                        status=status,
                        input=input,
                        output=output,
                        correlation_id=task_trace_id(thread_id),
                    )
                )
        except Exception:  # 审计是旁路:落库失败记日志,不阻断任务主流程
            logger.warning("AgentTask 审计写入失败(thread %s)", thread_id, exc_info=True)
