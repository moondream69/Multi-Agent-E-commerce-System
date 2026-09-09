"""AgentTask 审计与 PlanFailed 呈现测试(spec #8 遗留收敛)。

- 切片执行审计:图级注入记录器 → completed/failed 两类记录
- 审批决定审计:REST resume → approval_decision 记录(batch_id/decision/comment)
- PG 集成:PgAuditWriter 落 agent_tasks 行,correlation_id = 任务 trace_id(与 Langfuse 互链)
- PlanFailed:规划失败任务 → 任务行 result.error 落库(驾驶舱「未完成+原因」数据源)
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from python_backend.api.app import create_app
from python_backend.core.graph import build_supervisor
from python_backend.core.planning import PlanFailed
from python_backend.db.audit_store import PgAuditWriter
from python_backend.db.models import AgentTask, Task, User
from python_backend.db.session import SessionFactory
from python_backend.infrastructure.tracing import task_trace_id
from python_backend.settings import get_settings
from tests.conftest import (
    FakeApply,
    InMemoryApprovalBatchStore,
    RecordingAudit,
    StubPlanner,
    postgres_reachable,
    slice_agent,
)

PUBLISH = {
    "action": "product.publish",
    "params": {"product_id": 1},
    "snapshot": {"exists": True, "status": "draft"},
}


def _client(audit: RecordingAudit) -> AsyncClient:
    store = InMemoryApprovalBatchStore()
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent([], actions=[PUBLISH], answer="已登记")},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=FakeApply(),
        audit=audit,
    )
    return AsyncClient(
        transport=ASGITransport(
            app=create_app(graph=graph, batch_store=store, apply_fn=FakeApply(), audit=audit, auth_required=False)
        ),
        base_url="http://test",
    )


async def test_slice_execution_and_decision_audited() -> None:
    """切片执行审计(completed)+ 审批决定审计(batch_id/decision/comment 落记录)。"""
    audit = RecordingAudit()
    async with _client(audit) as client:
        created = await client.post("/api/tasks", json={"request": "上架商品"})
        thread_id = created.json()["thread_id"]
        batches = await client.get(f"/api/threads/{thread_id}/approvals")
        batch_id = batches.json()["approvals"][0]["batchId"]
        resumed = await client.post(
            f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "approve", "comment": "没问题"}}
        )
        assert resumed.status_code == 200

    slices = [r for r in audit.captured if r["type"] == "slice"]
    assert len(slices) == 1
    assert slices[0]["agent_id"] == "order_management"
    assert slices[0]["status"] == "completed"

    decisions = [r for r in audit.captured if r["type"] == "approval_decision"]
    assert len(decisions) == 1
    assert decisions[0]["status"] == "approve"
    assert decisions[0]["input"] == {"batch_id": batch_id, "comment": "没问题"}


async def test_slice_failure_audited() -> None:
    """子图 runner 抛错:failed 记录后异常如实上抛(500,不静默吞错)。"""
    audit = RecordingAudit()

    async def failing(slice_) -> dict:
        raise RuntimeError("子图爆炸")

    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": failing},
        checkpointer=InMemorySaver(),
        batch_store=InMemoryApprovalBatchStore(),
        audit=audit,
    )
    client = AsyncClient(
        transport=ASGITransport(
            app=create_app(graph=graph, audit=audit, auth_required=False), raise_app_exceptions=False
        ),
        base_url="http://test",
    )
    async with client:
        response = await client.post("/api/tasks", json={"request": "上架商品"})
    assert response.status_code == 500  # 编程错误如实上抛(现行语义)
    failed = [r for r in audit.captured if r["type"] == "slice" and r["status"] == "failed"]
    assert len(failed) == 1


async def test_plan_failed_recorded_in_task_result() -> None:
    """PlanFailed → API 响应 failed+error,任务行 result.error 落库(驾驶舱「未完成+原因」数据源)。"""

    class FailedPlanner:
        async def plan(self, request: str, context: str | None = None) -> PlanFailed:
            return PlanFailed("规划失败:步数超限")

    from python_backend.core.auth import create_token, hash_password

    async with SessionFactory() as session, session.begin():
        username = f"planfail-{uuid.uuid4().hex[:8]}"
        user = User(username=username, password_hash=hash_password("pw"))
        session.add(user)
        await session.flush()
        user_id = user.id

    graph = build_supervisor(FailedPlanner(), checkpointer=InMemorySaver(), batch_store=InMemoryApprovalBatchStore())
    client = AsyncClient(
        transport=ASGITransport(app=create_app(graph=graph, auth_required=True)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {create_token(username, user_id)}"},
    )
    async with client:
        created = await client.post("/api/tasks", json={"request": "任意需求"})
        thread_id = created.json()["thread_id"]
        assert created.json()["status"] == "failed"
        # 宪章「未完成+原因」语义:图 report_failure 加前缀
        assert created.json()["error"] == "未完成:规划失败:步数超限"

        detail = await client.get(f"/api/tasks/{thread_id}")
        assert detail.json()["result"]["error"] == "未完成:规划失败:步数超限"
        assert detail.json()["status"] == "failed"


# —— PG 集成(离线秒 skip) ——


@pytest.fixture(autouse=True)
def _require_postgres() -> None:
    if not postgres_reachable(get_settings().database_url):
        pytest.skip("Postgres 离线(compose dev 库),integration 跳过")


async def test_pg_audit_rows_land_with_trace_correlation() -> None:
    """PgAuditWriter:agent_tasks 行落库,correlation_id = 任务 trace_id(与 Langfuse 互链)。"""
    audit = PgAuditWriter()
    thread_id = f"t-{uuid.uuid4().hex[:8]}"
    async with SessionFactory() as session, session.begin():
        user = User(username=f"audit-{uuid.uuid4().hex[:8]}", password_hash="x")
        session.add(user)
        await session.flush()
        session.add(Task(thread_id=thread_id, user_id=user.id, session_id="audit-s", type="chat"))
    await audit.record(
        thread_id=thread_id,
        agent_id="order_management",
        type_="slice",
        status="completed",
        input={"description": "测试"},
    )
    async with SessionFactory() as session:
        row = (
            await session.execute(select(AgentTask).where(AgentTask.correlation_id == task_trace_id(thread_id)))
        ).scalar_one()
    assert row.type == "slice"
    assert row.status == "completed"
    assert row.agent_id == "order_management"
    assert row.task_id is not None  # 按 thread 反查关联任务行
