"""B4/B5 + B18 integration(spec #7 Testing):真 Postgres 上验证批次落库、apply 执行效果与 durable 恢复。

- B4:动作按类型打包落库(pending → approved/executed),批内同进同退的数据底座
- B5:挂起时"重启服务"(新 saver/图/客户端实例,同 PG)→ resume 从断点继续,不重放、不重复执行效果
- B18:apply 幂等(批次 executed 跳过)与真实效果落库
依赖 compose dev Postgres;离线时 TCP 探测秒 skip。
用 httpx AsyncClient 驱动:AsyncPostgresSaver 绑定 running loop,必须与图执行同 loop。
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select

from python_backend.api.app import create_app
from python_backend.core.graph import build_supervisor, supervisor_serde
from python_backend.db.approval_store import PostgresApprovalBatchStore
from python_backend.db.models import ApprovalBatch, Product
from python_backend.db.session import SessionFactory
from python_backend.settings import get_settings
from tests.conftest import StubPlanner, slice_agent

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("requires_postgres")]


async def make_assemblage(product_id: int | None = None):
    """完整生产装配(模拟单进程实例):async PG saver + PG store + 注入规划器 + 真实 apply。"""
    settings = get_settings()
    conn = await psycopg.AsyncConnection.connect(conninfo=settings.postgres_dsn, connect_timeout=5, autocommit=True)
    # ty 对 langgraph aio stubs 的 Conn 泛型报 invalid-argument-type(运行时合法,官方文档模式)
    saver = AsyncPostgresSaver(conn, serde=supervisor_serde())  # ty: ignore
    await saver.setup()
    store = PostgresApprovalBatchStore()
    target = product_id if product_id is not None else 0
    actions = [
        {
            "action": "product.publish",
            "params": {"product_id": target},
            "snapshot": {"exists": True, "status": "draft"},
        }
    ]
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent([], actions=actions, answer="上架动作已登记")},
        checkpointer=saver,
        batch_store=store,
        shadow_mode=False,  # B4/B5 验收审批语义(prod 行为);影子路径由图级单测覆盖
    )
    app = create_app(graph=graph, batch_store=store, auth_required=False)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), store


async def _make_product() -> Product:
    """创建测试商品(独立 SKU,避免与既有数据/并发测试互扰)。"""
    async with SessionFactory() as session:
        product = Product(sku=f"SKU-{uuid.uuid4().hex[:8]}", title="上架测试品", price=9.99, category="测试")
        session.add(product)
        await session.commit()
        return product


async def _pg_batches(thread_id: str) -> list[ApprovalBatch]:
    async with SessionFactory() as session:
        rows = (await session.execute(select(ApprovalBatch).where(ApprovalBatch.thread_id == thread_id))).scalars()
        return list(rows)


async def test_b4_batch_persisted_decided_and_applied_in_postgres() -> None:
    """B4:真实参数批次落库 → approve → apply 效果落库(商品上架)+ 批次 executed。"""
    product = await _make_product()
    client, _store = await make_assemblage(product.id)
    created = (await client.post("/api/tasks", json={"request": "上架商品"})).json()
    thread_id = created["thread_id"]
    assert created["status"] == "interrupted"

    batches = await _pg_batches(thread_id)
    assert len(batches) == 1
    assert batches[0].status == "pending"
    assert batches[0].action_type == "product.publish"
    assert batches[0].actions[0]["params"] == {"product_id": product.id}
    assert batches[0].mode == "approval"

    response = await client.post(
        f"/api/threads/{thread_id}/resume", json={batches[0].batch_id: {"decision": "approve"}}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    batches = await _pg_batches(thread_id)
    assert batches[0].status == "executed"
    assert batches[0].result == {"applied": True}
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None and row.status.value == "active", "apply 效果真实落库"
    await client.aclose()


async def test_b5_resume_survives_restart_without_replaying_effects() -> None:
    """B5:挂起后新建 saver/图/客户端实例(模拟重启)→ resume 从断点继续,apply 幂等(executed 跳过)。"""
    product = await _make_product()
    client_1, _store = await make_assemblage(product.id)
    thread_id = (await client_1.post("/api/tasks", json={"request": "上架商品"})).json()["thread_id"]
    batch_id = (await _pg_batches(thread_id))[0].batch_id
    await client_1.aclose()

    # —— "重启":全新 saver 连接、全新图、全新客户端,同 PG ——
    client_2, _store2 = await make_assemblage(product.id)

    approvals = (await client_2.get(f"/api/threads/{thread_id}/approvals")).json()["approvals"]
    assert len(approvals) == 1, "重启后挂起批次仍可见(状态在 PG)"
    assert approvals[0]["batchId"] == batch_id

    response = await client_2.post(f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "approve"}})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    batches = await _pg_batches(thread_id)
    assert len(batches) == 1, "重放不得重复落库(create_batch 幂等)"
    assert batches[0].status == "executed"
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None and row.status.value == "active"
    await client_2.aclose()
