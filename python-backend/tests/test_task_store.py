"""任务行存储缝(issue #13):TaskStore 协议的内存替身语义。

离线快速套件的端点流程用例经 create_app 注入本替身,不再触 PG;
替身须复现生产实现的可见语义(未认证跳过/缺行静默/倒序列表/归属反查),
端点路由断言见文末(认证态端到端)。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from python_backend.api.app import create_app
from python_backend.core.auth import create_token
from python_backend.core.graph import build_supervisor
from python_backend.db.models import TaskStatus
from tests.conftest import (
    FakeApply,
    InMemoryApprovalBatchStore,
    InMemorySessionMemory,
    InMemoryTaskStore,
    StubPlanner,
    slice_agent,
)


async def test_create_skips_unauthenticated_and_records_authenticated() -> None:
    """未认证(user_id None)create 跳过(生产同语义);认证上下文落行且初态 in_progress。"""
    store = InMemoryTaskStore()

    await store.create_task_row(thread_id="t-anon", user_id=None, session_id="s", type_="chat", request="未认证")
    assert await store.get_task("t-anon") is None

    await store.create_task_row(thread_id="t-1", user_id=7, session_id="s", type_="chat", request="上架商品")
    row = await store.get_task("t-1")
    assert row is not None
    assert row.status is TaskStatus.IN_PROGRESS
    assert row.input == {"request": "上架商品"}
    assert (row.session_id, row.user_id, row.type) == ("s", 7, "chat")


async def test_update_sets_fields_and_skips_missing_row() -> None:
    """update 落状态/计划/结果;缺行静默跳过(生产同语义:未认证路径无行);None 字段不覆写。"""
    store = InMemoryTaskStore()

    await store.update_task_row(thread_id="t-missing", status="failed", result={"error": "x"})  # 缺行:静默不抛

    await store.create_task_row(thread_id="t-1", user_id=7, session_id="s", type_="chat", request="上架商品")
    await store.update_task_row(
        thread_id="t-1", status="interrupted", slice_plan={"slices": []}, result={"summary": "已登记"}
    )
    row = await store.get_task("t-1")
    assert row is not None
    assert row.status is TaskStatus.INTERRUPTED
    assert row.slice_plan == {"slices": []}
    assert row.result == {"summary": "已登记"}

    await store.update_task_row(thread_id="t-1", status="completed")  # 仅状态:计划/结果保留
    assert row.status is TaskStatus.COMPLETED
    assert row.slice_plan == {"slices": []}
    assert row.result == {"summary": "已登记"}


async def test_task_session_looks_up_owner() -> None:
    """task_session 按 thread 反查 (session_id, user_id);无行(未认证跳过/未知线程)→ None。"""
    store = InMemoryTaskStore()

    await store.create_task_row(thread_id="t-anon", user_id=None, session_id="s", type_="chat", request="未认证")
    assert await store.task_session("t-anon") is None

    await store.create_task_row(thread_id="t-1", user_id=7, session_id="s-1", type_="chat", request="上架商品")
    assert await store.task_session("t-1") == ("s-1", 7)


async def test_list_tasks_newest_first_with_session_filter() -> None:
    """list_tasks 最新在前;session_id 给定时只含该会话(空串按假值不过滤,生产同语义)。"""
    store = InMemoryTaskStore()
    await store.create_task_row(thread_id="t-1", user_id=7, session_id="s-1", type_="chat", request="第一条")
    await store.create_task_row(thread_id="t-2", user_id=7, session_id="s-2", type_="chat", request="第二条")
    await store.create_task_row(thread_id="t-3", user_id=7, session_id="s-1", type_="chat", request="第三条")

    assert [row.thread_id for row in await store.list_tasks()] == ["t-3", "t-2", "t-1"]
    assert [row.thread_id for row in await store.list_tasks(session_id="s-1")] == ["t-3", "t-1"]
    assert [row.thread_id for row in await store.list_tasks(session_id="")] == ["t-3", "t-2", "t-1"]


# —— 端点路由(认证态端到端):读写都落在 create_app 注入的替身上,不触 PG ——


async def test_authenticated_task_flow_uses_injected_store() -> None:
    """认证态发起 → 替身落行(创建/终态更新)→ 详情读回:端点经 app.state.task_store 分发。"""
    task_store = InMemoryTaskStore()
    batch_store = InMemoryApprovalBatchStore()
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent([], answer="完成")},
        checkpointer=InMemorySaver(),
        batch_store=batch_store,
        apply_fn=FakeApply(),
    )
    client = TestClient(
        create_app(
            graph=graph,
            batch_store=batch_store,
            memory=InMemorySessionMemory(),
            task_store=task_store,
            auth_required=False,
        )
    )
    client.headers.update({"Authorization": f"Bearer {create_token('tester', 42)}"})

    response = client.post("/api/tasks", json={"request": "上架商品"})

    assert response.status_code == 201
    thread_id = response.json()["thread_id"]
    row = await task_store.get_task(thread_id)
    assert row is not None, "认证态任务行须落注入替身(端点不得绕过注入直连 PG)"
    assert row.status is TaskStatus.COMPLETED
    assert await task_store.task_session(thread_id) == ("default", 42)

    detail = client.get(f"/api/tasks/{thread_id}").json()
    assert detail["status"] == "completed"
    assert detail["request"] == "上架商品"
