"""WS 事件发射测试(spec #7 seam):图侧 approval.requested、REST 侧任务生命周期与 approval.decided。

发射器协议注入(RecordingEmitter),不依赖真实 socket 连接;真实 socket e2e 见 test_ws_server.py。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from python_backend.api.app import create_app
from python_backend.core.graph import SupervisorState, build_supervisor
from tests.conftest import (
    FakeApply,
    InMemoryApprovalBatchStore,
    RecordingEmitter,
    StubPlanner,
    slice_agent,
)

PUBLISH = {
    "action": "product.publish",
    "params": {"product_id": 1},
    "snapshot": {"exists": True, "status": "draft"},
}


async def test_graph_emits_approval_requested_once() -> None:
    """图侧:批次创建后广播 approval.requested(含批次真实参数);重放 resume 不重复广播。"""
    store = InMemoryApprovalBatchStore()
    emitter = RecordingEmitter()
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent([], actions=[PUBLISH], answer="已登记")},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=FakeApply(),
        emitter=emitter,
    )
    config = {"configurable": {"thread_id": "ws-requested"}}
    await graph.ainvoke(SupervisorState(request="上架商品", thread_id="ws-requested"), config)

    requested = [e for e in emitter.events if e[0] == "approval.requested"]
    assert len(requested) == 1
    payload = requested[0][1]
    assert payload["threadId"] == "ws-requested"
    assert payload["sliceNo"] == 1
    assert payload["agent"] == "order_management"
    assert payload["batches"][0]["actionType"] == "product.publish"
    assert payload["batches"][0]["actions"][0]["params"] == {"product_id": 1}

    # resume 重放:不重复广播 requested
    snapshot = await graph.aget_state(config)
    interrupt_ = snapshot.tasks[0].interrupts[0]
    batch_id = interrupt_.value["batches"][0]["batch_id"]
    await graph.ainvoke(
        Command(resume={interrupt_.id: {"terminate": False, "decisions": {batch_id: {"decision": "approve"}}}}),
        config,
    )
    assert len([e for e in emitter.events if e[0] == "approval.requested"]) == 1


def make_client() -> tuple[TestClient, InMemoryApprovalBatchStore, FakeApply, RecordingEmitter]:
    store = InMemoryApprovalBatchStore()
    apply_fn = FakeApply()
    emitter = RecordingEmitter()
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent([], actions=[PUBLISH], answer="已登记")},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=apply_fn,
        emitter=emitter,
    )
    client = TestClient(
        create_app(
            graph=graph,
            batch_store=store,
            apply_fn=apply_fn,
            emitter=emitter,
            auth_required=False,
        )
    )
    return client, store, apply_fn, emitter


@pytest.mark.integration  # 端点写任务行(PG),离线不可跑(issue #13)
@pytest.mark.usefixtures("requires_postgres")
async def test_rest_emits_task_lifecycle_events() -> None:
    """REST:任务发起广播 created + interrupted(中断时)。"""
    client, _store, _apply, emitter = make_client()
    client.post("/api/tasks", json={"request": "上架商品"})
    assert "task.created" in emitter.names()
    assert "task.interrupted" in emitter.names()


@pytest.mark.integration  # 端点写任务行(PG),离线不可跑(issue #13)
@pytest.mark.usefixtures("requires_postgres")
async def test_resume_emits_decided_and_completed() -> None:
    """REST:resume 广播 approval.decided + task.completed。"""
    client, store, _apply, emitter = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["thread_id"]
    batch_id = (await store.list_pending(thread_id))[0].batch_id

    client.post(f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "approve", "comment": "没问题"}})

    decided = [e for e in emitter.events if e[0] == "approval.decided"]
    assert len(decided) == 1
    assert decided[0][1] == {
        "threadId": thread_id,
        "batchId": batch_id,
        "decision": "approve",
        "comment": "没问题",
    }
    assert "task.completed" in emitter.names()


@pytest.mark.integration  # 端点写任务行(PG),离线不可跑(issue #13)
@pytest.mark.usefixtures("requires_postgres")
async def test_message_terminate_emits_decided_reject() -> None:
    """REST:自然消息终止 → 全部批次 decided(reject),任务失败终态事件。"""
    client, store, _apply, emitter = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["thread_id"]
    batch_id = (await store.list_pending(thread_id))[0].batch_id

    client.post(f"/api/threads/{thread_id}/message", json={"text": "算了"})

    decided = [e for e in emitter.events if e[0] == "approval.decided"]
    assert len(decided) == 1
    assert decided[0][1]["batchId"] == batch_id
    assert decided[0][1]["decision"] == "reject"
    assert "task.failed" in emitter.names(), "终止任务以 failed(带 error)终态呈现"


@pytest.mark.integration  # 端点写任务行(PG),离线不可跑(issue #13)
@pytest.mark.usefixtures("requires_postgres")
async def test_shadow_mode_emits_requested_without_interrupt() -> None:
    """影子模式:approval.requested 照发(审批中心展示影子段),无 task.interrupted。"""
    store = InMemoryApprovalBatchStore()
    emitter = RecordingEmitter()
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent([], actions=[PUBLISH])},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=FakeApply(),
        emitter=emitter,
        shadow_mode=True,
    )
    client = TestClient(
        create_app(
            graph=graph,
            batch_store=store,
            apply_fn=FakeApply(),
            emitter=emitter,
            auth_required=False,
        )
    )
    client.post("/api/tasks", json={"request": "上架商品"})
    assert "approval.requested" in emitter.names()
    assert "task.interrupted" not in emitter.names()
