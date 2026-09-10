"""多会话端点测试(spec #9 A2):会话列表 / 删除(挂起审批拒删)/ 任务按会话过滤。

- GET /api/conversations:当前用户会话,updated_at 倒序,标题 20 字截断
- DELETE /api/conversations/{session_id}:成功 / 404 / 有挂起审批 409
- GET /api/tasks?session_id=:只返回该会话任务(历史隔离)
- 空白会话惰性落库:未发消息的会话不在列表
依赖真 PG;离线秒 skip。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from python_backend.api.app import create_app
from python_backend.core.auth import create_token, hash_password
from python_backend.core.graph import build_supervisor
from python_backend.db.models import ApprovalBatch, ApprovalStatus, User
from python_backend.db.session import SessionFactory
from python_backend.settings import get_settings
from tests.conftest import InMemoryApprovalBatchStore, StubPlanner, postgres_reachable, slice_agent

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _require_postgres() -> None:
    if not postgres_reachable(get_settings().database_url):
        pytest.skip("Postgres 离线(compose dev 库),integration 跳过")


async def _seed_user() -> tuple[int, str]:
    async with SessionFactory() as session, session.begin():
        username = f"conv-{uuid.uuid4().hex[:8]}"
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


async def _run_task(client: AsyncClient, request: str, session_id: str) -> str:
    response = await client.post("/api/tasks", json={"request": request, "session_id": session_id})
    assert response.status_code == 201
    return response.json()["thread_id"]


async def test_conversation_list_and_lazy_creation() -> None:
    """会话列表:发过消息的会话可见(标题 20 字截断);无消息的会话不落库。"""
    user_id, username = await _seed_user()
    async with _client(user_id, username) as client:
        await _run_task(client, "第一个会话的首条消息内容超过二十个字用于截断断言", "s-one")

        listing = await client.get("/api/conversations")
        assert listing.status_code == 200
        conversations = listing.json()["conversations"]
        # 惰性落库:只有发过消息的会话才有行(新建未发言的会话不在列表)
        assert [c["sessionId"] for c in conversations] == ["s-one"]
        assert conversations[0]["title"] == "第一个会话的首条消息内容超过二十个字用于截断断言"[:20]
        assert conversations[0]["messageCount"] >= 1
        assert conversations[0]["updatedAt"]


async def test_conversations_are_user_scoped() -> None:
    """会话按用户隔离:另一用户看不到本用户的会话(A2 历史隔离)。"""
    user_id, username = await _seed_user()
    other_id, other_name = await _seed_user()
    async with _client(user_id, username) as client:
        await _run_task(client, "本用户会话", "s-scoped")
    async with _client(other_id, other_name) as client:
        listing = await client.get("/api/conversations")
        assert [c["sessionId"] for c in listing.json()["conversations"]] == []


async def test_task_list_filters_by_session() -> None:
    """任务列表按会话过滤:切换会话即切换历史视图(会话 id 唯一化,免跨轮次串扰)。"""
    user_id, username = await _seed_user()
    session_a, session_b = f"s-a-{uuid.uuid4().hex[:8]}", f"s-b-{uuid.uuid4().hex[:8]}"
    async with _client(user_id, username) as client:
        thread_a = await _run_task(client, "会话 A 的任务", session_a)
        thread_b = await _run_task(client, "会话 B 的任务", session_b)

        scoped = await client.get("/api/tasks", params={"session_id": session_a})
        assert [t["threadId"] for t in scoped.json()["tasks"]] == [thread_a]
        other = await client.get("/api/tasks", params={"session_id": session_b})
        assert [t["threadId"] for t in other.json()["tasks"]] == [thread_b]
        # 不传 session_id:兼容既有调用(全局列表)
        all_tasks = await client.get("/api/tasks")
        ids = {t["threadId"] for t in all_tasks.json()["tasks"]}
        assert {thread_a, thread_b} <= ids


async def test_delete_conversation() -> None:
    """删除会话:行消失,重复删除 404;任务行保留为审计(不随会话删除)。"""
    user_id, username = await _seed_user()
    async with _client(user_id, username) as client:
        thread_id = await _run_task(client, "待删除会话", "s-del")

        deleted = await client.delete("/api/conversations/s-del")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True}
        assert [c["sessionId"] for c in (await client.get("/api/conversations")).json()["conversations"]] == []
        again = await client.delete("/api/conversations/s-del")
        assert again.status_code == 404
        # 任务行仍在(审计),详情可查
        assert (await client.get(f"/api/tasks/{thread_id}")).status_code == 200


async def test_delete_conversation_with_pending_approval_409() -> None:
    """有挂起审批批次 → 409 拒删(先决定再删,spec #9 决策 1)。"""
    user_id, username = await _seed_user()
    async with _client(user_id, username) as client:
        thread_id = await _run_task(client, "含挂起审批的会话", "s-pending")
        async with SessionFactory() as session, session.begin():
            session.add(
                ApprovalBatch(
                    batch_id=str(uuid.uuid4()),
                    thread_id=thread_id,
                    slice_no=1,
                    action_type="product.publish",
                    actions=[],
                    status=ApprovalStatus.PENDING,
                    mode="approval",
                    requested_by="test",
                )
            )
        response = await client.delete("/api/conversations/s-pending")
        assert response.status_code == 409
        assert "挂起审批" in response.json()["detail"]
        # 会话仍在
        assert [c["sessionId"] for c in (await client.get("/api/conversations")).json()["conversations"]] == [
            "s-pending"
        ]


async def test_products_endpoint_lists_threshold() -> None:
    """GET /api/products(spec #9):只读列表含 stock 与 alertThreshold(模拟流量发现商品)。"""
    user_id, username = await _seed_user()
    async with _client(user_id, username) as client:
        response = await client.get("/api/products")
        assert response.status_code == 200
        products = response.json()["products"]
        assert products, "测试库应已有商品(其他测试/seed 累积)"
        assert {"id", "sku", "title", "price", "category", "status", "stock", "alertThreshold"} <= set(products[0])


# —— 会话重命名(spec #11 A2 扩展) ——


async def test_rename_conversation_and_auto_title_pin() -> None:
    """重命名:标题 trim 更新(响应与列表同形);手工命名不再被自动标题覆盖(自动仅在建行时写)。"""
    user_id, username = await _seed_user()
    async with _client(user_id, username) as client:
        await _run_task(client, "原始自动标题的会话首条消息", "s-rename")

        response = await client.patch("/api/conversations/s-rename", json={"title": "  改过的名字  "})
        assert response.status_code == 200
        conversation = response.json()["conversation"]
        assert conversation["title"] == "改过的名字"  # trim
        assert set(conversation) == {"sessionId", "title", "updatedAt", "messageCount"}
        listing = await client.get("/api/conversations")
        assert listing.json()["conversations"][0]["title"] == "改过的名字"

        # 再发消息:自动标题只在建行时写,不覆盖手工命名
        await _run_task(client, "第二条消息", "s-rename")
        listing = await client.get("/api/conversations")
        assert listing.json()["conversations"][0]["title"] == "改过的名字"


async def test_rename_validation_404_and_user_scope() -> None:
    """空/超长 → 422;不存在/他人会话 → 404(归属隔离,改名不生效)。"""
    user_id, username = await _seed_user()
    other_id, other_name = await _seed_user()
    async with _client(user_id, username) as client:
        await _run_task(client, "待重命名会话", "s-rename-v")
        assert (await client.patch("/api/conversations/s-rename-v", json={"title": "   "})).status_code == 422
        assert (await client.patch("/api/conversations/s-rename-v", json={"title": "长" * 51})).status_code == 422
        assert (await client.patch("/api/conversations/s-missing", json={"title": "x"})).status_code == 404
    async with _client(other_id, other_name) as client:
        assert (await client.patch("/api/conversations/s-rename-v", json={"title": "越权改名"})).status_code == 404
    async with _client(user_id, username) as client:
        listing = await client.get("/api/conversations")
        assert listing.json()["conversations"][0]["title"] == "待重命名会话"
