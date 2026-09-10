"""Langfuse 层级观测测试(spec #7 B14):thread 根 trace、规划/切片 span、审批事件记录与互链。

接缝:TaskTracer 协议注入(RecordingTaskTracer),不依赖 Langfuse SDK/服务。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from python_backend.api.app import create_app
from python_backend.core.graph import build_supervisor
from python_backend.infrastructure.tracing import LangfuseTaskTracer, NullTaskTracer
from tests.conftest import (
    FakeApply,
    InMemoryApprovalBatchStore,
    RecordingTaskTracer,
    StubPlanner,
    slice_agent,
)

PUBLISH = {
    "action": "product.publish",
    "params": {"product_id": 1},
    "snapshot": {"exists": True, "status": "draft"},
}


def make_client() -> tuple[TestClient, InMemoryApprovalBatchStore, RecordingTaskTracer]:
    store = InMemoryApprovalBatchStore()
    tracer = RecordingTaskTracer()
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent([], actions=[PUBLISH], answer="已登记")},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=FakeApply(),
        tracer=tracer,
    )
    client = TestClient(
        create_app(graph=graph, batch_store=store, apply_fn=FakeApply(), tracer=tracer, auth_required=False)
    )
    return client, store, tracer


@pytest.mark.integration  # 端点写任务行(PG),离线不可跑(issue #13)
@pytest.mark.usefixtures("requires_postgres")
async def test_task_trace_spans_and_approval_events_recorded() -> None:
    """B14:任务 trace 内记录 manager 规划 span、切片 span、approval.requested 事件(带 batchId 互链)。"""
    client, store, tracer = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["thread_id"]

    assert tracer.traces == [thread_id], "REST 层以 thread_id 为名开启任务 trace"
    assert ("manager.plan", {"request": "上架商品"}) in tracer.spans
    slice_spans = [s for s in tracer.spans if s[0] == "slice.order_management"]
    assert len(slice_spans) == 1
    assert slice_spans[0][1] == {"description": "上架商品"}

    requested = [e for e in tracer.events if e[0] == "approval.requested"]
    assert len(requested) == 1
    batch_ids = requested[0][1]["batchIds"]
    assert len(batch_ids) == 1
    assert requested[0][1]["threadId"] == thread_id

    # 决定:approval.decided 事件带 batchId + decision(与审计行互链)
    batch_id = (await store.list_pending(thread_id))[0].batch_id
    client.post(f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "approve", "comment": "没问题"}})

    decided = [e for e in tracer.events if e[0] == "approval.decided"]
    assert len(decided) == 1
    assert decided[0][1] == {
        "threadId": thread_id,
        "batchId": batch_id,
        "decision": "approve",
        "comment": "没问题",
    }
    assert tracer.traces.count(thread_id) == 2, (
        "resume 阶段同样开启任务 trace(协议层两次调用,Langfuse 层经确定性 trace_id 合并)"
    )


async def test_null_tracer_is_safe_noop() -> None:
    """未配置 Langfuse 时 NullTaskTracer 全部 no-op。"""
    tracer = NullTaskTracer()
    with tracer.trace("t"), tracer.span("s", input={}):
        tracer.record_event("e", {})
    assert True


def test_langfuse_tracer_disabled_when_unconfigured() -> None:
    """LangfuseTaskTracer 在未配置 host 时不初始化 SDK(由 settings 决定,不断言环境配置)。"""
    tracer = LangfuseTaskTracer()
    with tracer.trace("t"), tracer.span("s"):
        tracer.record_event("e", {})
    assert True  # 未抛错即为 no-op 或成功记录


class FakeObservation:
    """假 observation:上下文管理器,记录 end。"""

    def __init__(self) -> None:
        self.ended = False

    def __enter__(self) -> FakeObservation:
        return self

    def __exit__(self, *args: object) -> bool:
        self.ended = True
        return False

    def end(self) -> None:
        self.ended = True


class FakeLangfuseClient:
    """假 Langfuse client:记录调用,不依赖真 SDK(验证启用路径的 API 用法)。"""

    def __init__(self) -> None:
        self.current_observations: list[dict] = []
        self.observations: list[dict] = []
        self.events: list[dict] = []

    def start_as_current_observation(self, **kwargs: object) -> FakeObservation:
        self.current_observations.append(kwargs)
        return FakeObservation()

    def start_observation(self, **kwargs: object) -> FakeObservation:
        self.observations.append(kwargs)
        return FakeObservation()


def test_langfuse_tracer_enabled_path_uses_context_manager_api() -> None:
    """启用路径(B14 + spec #8 resume_trace):trace/span 走 start_as_current_observation(上下文管理器 API),
    trace_id 由 thread_id 确定性派生(跨 resume 合并同一 trace);record_event 走 start_observation + end;
    langfuse 4.x 的 start_observation 返回值不是上下文管理器。"""
    client = FakeLangfuseClient()
    tracer = LangfuseTaskTracer(client=client)

    with tracer.trace("t1"), tracer.span("manager.plan", input={"request": "x"}):
        tracer.record_event("approval.requested", {"batchIds": ["b1"]})
    with tracer.trace("t1"):  # 第二阶段(resume):同一 trace_id,不分裂
        pass

    assert client.current_observations == [
        {"name": "task:t1", "as_type": "span", "trace_id": "task-t1"},
        {"name": "manager.plan", "as_type": "span", "input": {"request": "x"}},
        {"name": "task:t1", "as_type": "span", "trace_id": "task-t1"},
    ]
    assert client.observations == [{"name": "approval.requested", "as_type": "span", "output": {"batchIds": ["b1"]}}]
