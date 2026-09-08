"""B4/B5 integration(spec #6 Testing):真 Postgres 上验证审批批次落库与 durable 恢复。

- B4:批次打包落库(pending → decided),批内同进同退
- B5:挂起时"重启服务"(新 saver/图/客户端实例,同 PG)→ resume 从断点继续,不重放
依赖 compose dev Postgres;离线时 TCP 探测秒 skip。
用 httpx AsyncClient 驱动:AsyncPostgresSaver 绑定 running loop,必须与图执行同 loop。
"""

from __future__ import annotations

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select

from python_backend.api.app import create_app
from python_backend.core.graph import build_supervisor, supervisor_serde
from python_backend.db.approval_store import PostgresApprovalBatchStore
from python_backend.db.models import ApprovalBatch
from python_backend.db.session import SessionFactory
from python_backend.settings import get_settings
from tests.conftest import StubPlanner, postgres_reachable, slice_agent

pytestmark = pytest.mark.integration


async def make_assemblage() -> AsyncClient:
    """完整生产装配(模拟单进程实例):async PG saver + PG store + 注入规划器。"""
    settings = get_settings()
    conn = await psycopg.AsyncConnection.connect(conninfo=settings.postgres_dsn, connect_timeout=5, autocommit=True)
    # ty 对 langgraph aio stubs 的 Conn 泛型报 invalid-argument-type(运行时合法,官方文档模式)
    saver = AsyncPostgresSaver(conn, serde=supervisor_serde())  # ty: ignore
    await saver.setup()
    store = PostgresApprovalBatchStore()
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent([])},
        checkpointer=saver,
        batch_store=store,
        shadow_mode=False,  # B4/B5 验收审批语义(prod 行为);影子路径由图级单测覆盖
    )
    app = create_app(graph=graph, batch_store=store)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _require_postgres() -> None:
    if not postgres_reachable(get_settings().database_url):
        pytest.skip("Postgres 离线(compose dev 库),integration 跳过")


async def _pg_batches(thread_id: str) -> list[ApprovalBatch]:
    async with SessionFactory() as session:
        rows = (await session.execute(select(ApprovalBatch).where(ApprovalBatch.thread_id == thread_id))).scalars()
        return list(rows)


async def test_b4_batch_persisted_and_decided_in_postgres() -> None:
    """B4:批次打包落库、决定落库(批内同进同退的数据底座)。"""
    client = await make_assemblage()
    created = (await client.post("/api/tasks", json={"request": "上架商品"})).json()
    thread_id = created["thread_id"]

    batches = await _pg_batches(thread_id)
    assert len(batches) == 1
    assert batches[0].status == "pending"
    assert batches[0].slice_no == 1
    assert batches[0].action_type == "上架审批"
    assert batches[0].actions == [{"description": "上架商品", "approval_points": ["上架审批"]}]
    assert batches[0].mode == "approval"

    batch_id = batches[0].batch_id
    response = await client.post(f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "approve"}})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    batches = await _pg_batches(thread_id)
    assert batches[0].status == "approved"
    await client.aclose()


async def test_b5_resume_survives_service_restart_without_replay() -> None:
    """B5:挂起后新建 saver/图/客户端实例(模拟重启)→ resume 从断点继续,不重放已完成段。"""
    client_1 = await make_assemblage()
    thread_id = (await client_1.post("/api/tasks", json={"request": "上架商品"})).json()["thread_id"]
    batch_id = (await _pg_batches(thread_id))[0].batch_id
    await client_1.aclose()

    # —— "重启":全新 saver 连接、全新图、全新客户端,同 PG ——
    client_2 = await make_assemblage()

    approvals = (await client_2.get(f"/api/threads/{thread_id}/approvals")).json()["approvals"]
    assert len(approvals) == 1, "重启后挂起批次仍可见(状态在 PG)"
    assert approvals[0]["batchId"] == batch_id

    response = await client_2.post(f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "approve"}})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    batches = await _pg_batches(thread_id)
    assert len(batches) == 1, "重放不得重复落库(create_batch 幂等)"
    assert batches[0].status == "approved"
    await client_2.aclose()
