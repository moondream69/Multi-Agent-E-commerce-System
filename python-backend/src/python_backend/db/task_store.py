"""任务行存储(spec #8):tasks 表读写——驾驶舱列表/详情数据源 + resume 记忆归属查询。

任务行在 create_task 落库(thread_id 唯一),状态随生命周期更新(interrupted/completed/failed);
resume 时按 thread_id 反查 session_id/user_id,供会话记忆续写。

TaskStore 协议:端点经 create_app 注入(生产 PostgresTaskStore,测试内存替身)——离线快速套件不触库。
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import select

from python_backend.db.models import Task, TaskStatus
from python_backend.db.session import SessionFactory


class TaskStore(Protocol):
    """任务行存储协议(issue #13):五操作与端点可见语义一致——未认证 create 跳过、缺行静默跳过、列表倒序。"""

    async def create_task_row(
        self, *, thread_id: str, user_id: int | None, session_id: str, type_: str, request: str
    ) -> None:
        """任务发起落库;未认证上下文(user_id None)跳过(非认证测试路径)。"""
        ...

    async def update_task_row(
        self, *, thread_id: str, status: str, slice_plan: dict | None = None, result: dict | None = None
    ) -> None:
        """任务生命周期更新(状态/切片计划/结果);行不存在静默跳过(未认证路径无行)。"""
        ...

    async def task_session(self, thread_id: str) -> tuple[str, int] | None:
        """按 thread_id 反查 (session_id, user_id):resume 时记忆归属查询。"""
        ...

    async def list_tasks(self, *, session_id: str | None = None) -> list[Task]:
        """任务列表(驾驶舱数据源):最新在前;session_id 给定时只返回该会话的任务(A2 历史隔离)。"""
        ...

    async def get_task(self, thread_id: str) -> Task | None:
        """任务详情行。"""
        ...


class PostgresTaskStore(TaskStore):
    """PG 实现(tasks 表):issue #13 由模块函数抽为可注入实现,行为零变化。"""

    async def create_task_row(
        self, *, thread_id: str, user_id: int | None, session_id: str, type_: str, request: str
    ) -> None:
        if user_id is None:
            return
        async with SessionFactory() as session, session.begin():
            session.add(
                Task(
                    thread_id=thread_id,
                    user_id=user_id,
                    session_id=session_id,
                    type=type_,
                    status=TaskStatus.IN_PROGRESS,
                    input={"request": request},
                )
            )

    async def update_task_row(
        self, *, thread_id: str, status: str, slice_plan: dict | None = None, result: dict | None = None
    ) -> None:
        async with SessionFactory() as session, session.begin():
            row = (await session.execute(select(Task).where(Task.thread_id == thread_id))).scalar_one_or_none()
            if row is None:
                return
            row.status = TaskStatus(status)
            if slice_plan is not None:
                row.slice_plan = slice_plan
            if result is not None:
                row.result = result

    async def task_session(self, thread_id: str) -> tuple[str, int] | None:
        async with SessionFactory() as session:
            row = (await session.execute(select(Task).where(Task.thread_id == thread_id))).scalar_one_or_none()
            if row is None:
                return None
            return row.session_id, row.user_id

    async def list_tasks(self, *, session_id: str | None = None) -> list[Task]:
        async with SessionFactory() as session:
            statement = select(Task).order_by(Task.created_at.desc())
            if session_id:
                statement = statement.where(Task.session_id == session_id)
            rows = (await session.execute(statement)).scalars().all()
            return list(rows)

    async def get_task(self, thread_id: str) -> Task | None:
        async with SessionFactory() as session:
            return (await session.execute(select(Task).where(Task.thread_id == thread_id))).scalar_one_or_none()
