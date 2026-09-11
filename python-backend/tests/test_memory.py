"""会话记忆测试(spec #8 B16):短上下文组装 + 超窗摘要 + 规划注入 + 任务流落库。

- 单测:get_context 最近 N 轮组装、ManagerPlanner 携上下文入 LLM 消息、图级 context 透传
- 集成(真 PG):record 落库与摘要压缩(FakeLlm)、create_task 全链路(任务行+记忆双落)
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from python_backend.api.app import create_app
from python_backend.core.auth import create_token, hash_password
from python_backend.core.graph import build_supervisor
from python_backend.core.memory import CONTEXT_MESSAGES, PostgresSessionMemory
from python_backend.core.planning import ManagerPlanner
from python_backend.db.models import Conversation, Task, User
from python_backend.db.session import SessionFactory
from tests.conftest import FakeLlm, InMemoryApprovalBatchStore, StubPlanner


class InMemorySessionMemory:
    """内存假记忆:captured 记录 record 调用,context 脚本化 get_context 返回值。"""

    def __init__(self, context: str | None = None) -> None:
        self.context = context
        self.recorded: list[dict] = []

    async def get_context(self, session_id: str, user_id: int | None) -> str | None:
        return self.context

    async def record(
        self, session_id: str, user_id: int | None, *, role: str, content: str, task_id: str | None = None
    ) -> None:
        self.recorded.append({"session_id": session_id, "role": role, "content": content, "task_id": task_id})


# —— 单测(离线) ——


async def test_planner_composes_context_into_llm_messages() -> None:
    """ManagerPlanner 携会话上下文入 LLM 用户消息(历史+当前需求)。"""
    llm = FakeLlm(responses=['{"slices": [{"no": 1, "agent": "order_management", "description": "x"}]}'])
    planner = ManagerPlanner(llm=llm)
    plan = await planner.plan("查一下订单", context="用户: 昨天问了库存\n助手: 已答复")
    assert plan is not None
    user_message = llm.calls[0]["messages"][1]["content"]
    assert "会话历史" in user_message and "当前需求:查一下订单" in user_message


async def test_graph_passes_context_to_planner() -> None:
    """监督图透传 context 到规划器(B16 注入点)。"""
    planner = StubPlanner()
    store = InMemoryApprovalBatchStore()
    graph = build_supervisor(planner, agents={}, checkpointer=InMemorySaver(), batch_store=store)
    from python_backend.core.graph import SupervisorState

    await graph.ainvoke(
        SupervisorState(request="上架商品", thread_id="t-1", context="用户: 上一条消息"),
        {"configurable": {"thread_id": "t-1"}},
    )
    assert planner.contexts[0] == "用户: 上一条消息"


# —— 集成(真 PG,离线秒 skip) ——


pytestmark = pytest.mark.usefixtures("requires_postgres")


async def _seed_user() -> tuple[int, str]:
    async with SessionFactory() as session, session.begin():
        username = f"mem-{uuid.uuid4().hex[:8]}"
        user = User(username=username, password_hash=hash_password("pw"))
        session.add(user)
        await session.flush()
        return user.id, username


async def test_pg_memory_records_and_reads_context() -> None:
    """record 落库;get_context 返回摘要+最近 N 轮;超窗触发摘要压缩(FakeLlm)。"""
    user_id, _ = await _seed_user()
    session_id = f"s-{uuid.uuid4().hex[:8]}"
    summarizer = FakeLlm(responses=["早期对话要点:讨论库存与上架。"])
    memory = PostgresSessionMemory(llm=summarizer)

    # 冷启动:无历史 → None
    assert await memory.get_context(session_id, user_id) is None

    # 少量消息:get_context 全部返回(≤N)
    await memory.record(session_id, user_id, role="user", content="第一条")
    await memory.record(session_id, user_id, role="assistant", content="第二条答复")
    context = await memory.get_context(session_id, user_id)
    assert "用户: 第一条" in (context or "") and "助手: 第二条答复" in (context or "")

    # 超窗(>12):触发摘要,消息裁剪到最近 N 条,summary 落库
    for index in range(13):
        await memory.record(session_id, user_id, role="user", content=f"第{index}轮")
    async with SessionFactory() as session:
        row = (
            await session.execute(
                select(Conversation).where(Conversation.user_id == user_id, Conversation.session_id == session_id)
            )
        ).scalar_one()
    assert row.summary is not None and row.summary["text"]
    # 压缩后又有 2 条进入 → 6+2=8,仍未超窗(不重复摘要)
    assert len(row.messages) == CONTEXT_MESSAGES + 2


async def test_create_task_wires_memory_and_task_row() -> None:
    """create_task 全链路:用户消息入记忆、任务行落库(session_id 归属)、上下文注入图。"""
    user_id, username = await _seed_user()
    memory = InMemorySessionMemory(context="用户: 上次聊过库存")
    store = InMemoryApprovalBatchStore()
    graph = build_supervisor(StubPlanner(), agents={}, checkpointer=InMemorySaver(), batch_store=store)
    token = create_token(username, user_id)
    client = AsyncClient(
        transport=ASGITransport(app=create_app(graph=graph, batch_store=store, memory=memory, auth_required=True)),
        base_url="http://test",
    )
    async with client:
        response = await client.post(
            "/api/tasks",
            json={"request": "上架商品", "session_id": "mem-session"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code in (200, 201)

    # 记忆:用户消息已落;任务行:thread/session 归属正确
    assert memory.recorded and memory.recorded[0]["role"] == "user"
    assert memory.recorded[0]["content"] == "上架商品"
    assert memory.recorded[0]["session_id"] == "mem-session"
    thread_id = response.json()["threadId"]
    async with SessionFactory() as session:
        task = (await session.execute(select(Task).where(Task.thread_id == thread_id))).scalar_one()
    assert task.user_id == user_id
    assert task.session_id == "mem-session"
    assert task.status.value in ("completed", "interrupted", "failed")
