"""REST 四端点 + 自然消息意图判定(切片 4,spec #6 D4)。

接缝:HTTP 接口(create_app 注入图与批次存储)与意图判定纯函数。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from python_backend.api.app import create_app
from python_backend.core.approvals import parse_decision_intent
from python_backend.core.graph import build_supervisor
from tests.conftest import InMemoryApprovalBatchStore, StubPlanner, slice_agent


def make_client() -> tuple[TestClient, InMemoryApprovalBatchStore]:
    store = InMemoryApprovalBatchStore()
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent()},
        checkpointer=InMemorySaver(),
        batch_store=store,
    )
    return TestClient(create_app(graph=graph, batch_store=store)), store


async def test_create_task_interrupts_with_approval_point() -> None:
    """POST /api/tasks:带审批切片的任务发起后状态 interrupted。"""
    client, _ = make_client()
    response = client.post("/api/tasks", json={"request": "上架商品"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "interrupted"
    assert body["thread_id"]


async def test_list_pending_approvals() -> None:
    """GET /approvals:挂起批次以切片语义暴露(batch_id/action_type/actions 快照)。"""
    client, _ = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["thread_id"]

    response = client.get(f"/api/threads/{thread_id}/approvals")

    assert response.status_code == 200
    approvals = response.json()["approvals"]
    assert len(approvals) == 1
    batch = approvals[0]
    assert batch["status"] == "pending"
    assert batch["sliceNo"] == 1
    assert batch["actionType"] == "上架审批"
    assert batch["actions"][0]["approvalPoints"] == ["上架审批"]


async def test_resume_with_approve_completes_thread() -> None:
    """POST /resume(按钮入口):approve 决定 → 图恢复执行 → 完成。"""
    client, store = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["thread_id"]
    batch_id = (await store.list_pending(thread_id))[0].batch_id

    response = client.post(f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "approve"}})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["summary"]["results"]["1"]["executed"] is True
    assert await store.list_pending(thread_id) == []


async def test_resume_with_reject_updates_batch_and_terminates_replan() -> None:
    """POST /resume:reject 决定 → 批次落 rejected,图回流后按冲突终止(StubPlanner 静态计划)。"""
    client, store = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["thread_id"]
    batch_id = (await store.list_pending(thread_id))[0].batch_id

    response = client.post(
        f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "reject", "comment": "价格太低"}}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    batch = next(r for r in store._by_id.values() if r.batch_id == batch_id)
    assert batch.status == "rejected"
    assert batch.comment == "价格太低"
    assert body["error"], "重规划冲突应如实上报「未完成+原因」"


async def test_resume_unknown_batch_is_404() -> None:
    """resume 不存在的批次 → 404。"""
    client, _ = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["thread_id"]
    response = client.post(f"/api/threads/{thread_id}/resume", json={"no-such-batch": {"decision": "approve"}})
    assert response.status_code == 404


async def test_natural_message_approve_resumes_pending_batch() -> None:
    """POST /message(自然消息入口):「同意」→ 挂起批次 approve → 完成。"""
    client, store = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["thread_id"]

    response = client.post(f"/api/threads/{thread_id}/message", json={"text": "同意"})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert await store.list_pending(thread_id) == []


async def test_natural_message_without_pending_is_409() -> None:
    """无挂起批次时发决定消息 → 409。"""
    client, _ = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["thread_id"]
    client.post(f"/api/threads/{thread_id}/message", json={"text": "同意"})
    response = client.post(f"/api/threads/{thread_id}/message", json={"text": "同意"})
    assert response.status_code == 409


async def test_natural_message_terminate_ends_thread_without_replan() -> None:
    """POST /message「算了」→ terminate:批次落 rejected、图「用户终止」结束、不回流重规划。"""
    client, store = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["thread_id"]

    response = client.post(f"/api/threads/{thread_id}/message", json={"text": "算了"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["error"] == "用户终止任务"
    batch = next(r for r in store._by_id.values() if r.thread_id == thread_id)
    assert batch.status == "rejected"
    assert "算了" in (batch.comment or "")


async def test_unrecognized_intent_is_422() -> None:
    """无法识别的自然消息 → 422。"""
    client, _ = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["thread_id"]
    response = client.post(f"/api/threads/{thread_id}/message", json={"text": "今天天气不错"})
    assert response.status_code == 422


def test_parse_decision_intent_priority() -> None:
    """意图判定:terminate > reject > approve(「不同意」不得误判为 approve)。"""
    assert parse_decision_intent("同意") == "approve"
    assert parse_decision_intent("不同意") == "reject"
    assert parse_decision_intent("不通过") == "reject"
    assert parse_decision_intent("取消任务") == "terminate"
    assert parse_decision_intent("今天天气不错") is None
