"""任务列表/详情端点测试(spec #8 驾驶舱数据源)。

- GET /api/tasks:列表含 threadId/sessionId/status/title(20 字截断)
- GET /api/tasks/{thread_id}:规划/结果/批次齐全;未知线程 404
依赖真 PG(任务行落库);离线秒 skip。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from python_backend.api.app import create_app
from python_backend.core.auth import create_token, hash_password
from python_backend.core.graph import build_supervisor
from python_backend.db.models import User
from python_backend.db.session import SessionFactory
from tests.conftest import InMemoryApprovalBatchStore, StubPlanner, slice_agent

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("requires_postgres")]


async def _seed_user() -> tuple[int, str]:
    async with SessionFactory() as session, session.begin():
        username = f"task-{uuid.uuid4().hex[:8]}"
        user = User(username=username, password_hash=hash_password("pw"))
        session.add(user)
        await session.flush()
        return user.id, username


def _client(user_id: int, username: str) -> AsyncClient:
    store = InMemoryApprovalBatchStore()
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent([], actions=[], answer="切片完成")},
        checkpointer=InMemorySaver(),
        batch_store=store,
    )
    return AsyncClient(
        transport=ASGITransport(app=create_app(graph=graph, batch_store=store, auth_required=True)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {create_token(username, user_id)}"},
    )


async def test_task_list_and_detail() -> None:
    """任务发起 → 列表可见(标题截断 20 字)→ 详情含 plan/results/batches。"""
    user_id, username = await _seed_user()
    request_text = "上架商品并检查库存状态变更流程走一遍看结果"
    async with _client(user_id, username) as client:
        created = await client.post("/api/tasks", json={"request": request_text, "session_id": "cockpit-s"})
        assert created.status_code == 201
        thread_id = created.json()["thread_id"]

        listing = await client.get("/api/tasks")
        assert listing.status_code == 200
        tasks = listing.json()["tasks"]
        assert any(task["threadId"] == thread_id for task in tasks)
        mine = next(task for task in tasks if task["threadId"] == thread_id)
        assert mine["title"] == request_text[:20]
        assert mine["status"] == "completed"
        assert mine["sessionId"] == "cockpit-s"

        detail = await client.get(f"/api/tasks/{thread_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["request"] == request_text
        assert body["plan"] is not None and body["plan"]["slices"][0]["agent"] == "order_management"
        assert body["results"] is not None and "1" in body["results"]
        assert body["batches"] == []

        missing = await client.get("/api/tasks/nonexistent-thread")
        assert missing.status_code == 404
