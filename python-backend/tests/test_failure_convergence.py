"""子图 LLM 失败收敛(issue #10):切片如实「未完成+原因」,异常路径不悬挂任务行。

三个接缝:
1. make_agent_runner:单点捕获 LlmFailure → incomplete(三个业务 Agent 一次覆盖,不穿透 REST 500)
2. 监督图:_aggregate 转 error「未完成+原因」、审计如实 failed、失败切片不进 apply
3. REST 两入口(发起/恢复):任何未预期异常 → 行 failed + 广播 task.failed,随后原样上抛 500
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from python_backend.agents.base import make_agent_runner
from python_backend.agents.customer_service.agent import build_customer_agent
from python_backend.api.app import create_app
from python_backend.core.auth import create_token, hash_password
from python_backend.core.graph import SupervisorState, build_supervisor
from python_backend.core.planning import Slice, SlicePlan
from python_backend.db.models import User
from python_backend.db.session import SessionFactory
from python_backend.infrastructure.llm import LlmFailure
from python_backend.settings import get_settings
from tests.conftest import (
    FakeApply,
    FakeExecutor,
    FakeLlm,
    InMemoryApprovalBatchStore,
    RecordingAudit,
    RecordingEmitter,
    StubPlanner,
    postgres_reachable,
    slice_agent,
)

LLM_DOWN = LlmFailure("LLM 调用失败:HTTP 400")
PUBLISH = {
    "action": "product.publish",
    "params": {"product_id": 1},
    "snapshot": {"exists": True, "status": "draft"},
}


def _failing_customer_graph():
    """客服子图 + 首轮 LLM 即失败(issue #10 实测触发路径:verify/draft 节点内 LlmFailure)。"""
    return build_customer_agent(executor=FakeExecutor(), llm=FakeLlm(tool_rounds=[LLM_DOWN]))[0]


def _customer_plan() -> SlicePlan:
    """单客服切片计划(agent 键与图域一致,避免夹具名实不符)。"""
    return SlicePlan(slices=[Slice(no=1, agent="customer_service", description="回复买家")])


def _two_slice_plan() -> SlicePlan:
    """切片 1 挂审批 → 切片 2 依赖 1,在恢复后执行(恢复入口的失败注入点)。"""
    return SlicePlan(
        slices=[
            Slice(no=1, agent="order_management", description="上架商品"),
            Slice(no=2, agent="customer_service", description="回复买家", depends_on=[1]),
        ]
    )


class FailingMemory:
    """记忆写入失败(仅助手侧):模拟图成功后的簿记失败(端点尾部兜底的注入点)。"""

    async def get_context(self, session_id: str, user_id: int | None) -> str | None:
        return None

    async def record(self, session_id, user_id, *, role: str, content: str, task_id: str | None = None) -> None:
        if role == "assistant":
            raise RuntimeError("记忆写入失败")


def _make_client(
    agents: dict,
    *,
    plan: SlicePlan | None = None,
    memory=None,
    raise_app_exceptions: bool = True,
) -> tuple[TestClient, InMemoryApprovalBatchStore, RecordingEmitter]:
    store = InMemoryApprovalBatchStore()
    emitter = RecordingEmitter()
    apply_fn = FakeApply()
    graph = build_supervisor(
        StubPlanner(plan),
        agents=agents,
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=apply_fn,
        emitter=emitter,
    )
    client = TestClient(
        create_app(
            graph=graph, batch_store=store, apply_fn=apply_fn, emitter=emitter, memory=memory, auth_required=False
        ),
        raise_server_exceptions=raise_app_exceptions,
    )
    return client, store, emitter


def _start_and_approve(client: TestClient):
    """发起两切片任务 → 切片 1 挂审批 → 批准 → 切片 2 在恢复中执行;返回 resume 响应。"""
    thread_id = client.post("/api/tasks", json={"request": "上架并回复"}).json()["thread_id"]
    batch_id = client.get(f"/api/threads/{thread_id}/approvals").json()["approvals"][0]["batchId"]
    return client.post(f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "approve", "comment": ""}})


# —— 接缝 1:make_agent_runner 单点捕获(三个业务 Agent 共用) ——


async def test_agent_runner_converts_llm_failure_to_incomplete() -> None:
    """子图内 LLM 失败 → runner 如实返回「未完成+原因」,不穿透为异常。"""
    runner = make_agent_runner(_failing_customer_graph())

    result = await runner(Slice(no=1, agent="customer_service", description="回复买家"))

    assert result["answer"] is None
    assert result["actions"] == [], "失败切片已收集的审批动作快照丢弃(切片判未完成,重新发起即可)"
    assert "HTTP 400" in result["incomplete"]


async def test_agent_runner_propagates_programming_error() -> None:
    """非 LlmFailure(编程错误)不由 runner 承接,原样上抛(永不静默吞错)。"""
    graph, _ = build_customer_agent(executor=FakeExecutor(), llm=FakeLlm(tool_rounds=[KeyError("state 缺字段")]))
    runner = make_agent_runner(graph)

    with pytest.raises(KeyError):
        await runner(Slice(no=1, agent="customer_service", description="回复买家"))


# —— 接缝 2:监督图聚合、审计、不进 apply ——


async def test_supervisor_aggregates_llm_failure_without_apply() -> None:
    """失败切片 → error「未完成+原因」;无批次不进 apply;审计如实 failed。"""
    audit = RecordingAudit()
    store = InMemoryApprovalBatchStore()
    apply_fn = FakeApply()
    graph = build_supervisor(
        StubPlanner(_customer_plan()),
        agents={"customer_service": make_agent_runner(_failing_customer_graph())},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=apply_fn,
        audit=audit,
    )

    config = {"configurable": {"thread_id": "llm-fail-graph"}}
    result = await graph.ainvoke(SupervisorState(request="回复买家", thread_id="llm-fail-graph"), config)

    assert result["error"] is not None
    assert "未完成" in result["error"] and "HTTP 400" in result["error"]
    assert apply_fn.calls == []
    assert store.batches == []
    slices = [r for r in audit.captured if r["type"] == "slice"]
    assert [r["status"] for r in slices] == ["failed"], "审计不得因捕获而误记为 completed"


# —— 接缝 3:发起入口 ——


def test_create_task_llm_failure_returns_failed_and_broadcasts() -> None:
    """LLM 失败走「如实未完成」通道:201 + status=failed + error;广播 task.failed(不挂起)。"""
    client, _store, emitter = _make_client(
        {"customer_service": make_agent_runner(_failing_customer_graph())}, plan=_customer_plan()
    )

    response = client.post("/api/tasks", json={"request": "回复买家"})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    assert "HTTP 400" in body["error"]
    assert "task.failed" in emitter.names()
    assert "task.interrupted" not in emitter.names()


def test_create_task_unexpected_error_converges_then_raises() -> None:
    """编程错误:行收敛 failed + 广播 task.failed,端点仍 500(异常原样上抛)。"""

    async def boom(slice_) -> dict:
        raise KeyError("编程错误")

    client, _store, emitter = _make_client({"order_management": boom}, raise_app_exceptions=False)

    response = client.post("/api/tasks", json={"request": "上架商品"})

    assert response.status_code == 500
    assert "task.failed" in emitter.names()


# —— 接缝 3:恢复入口(恢复过程中子图失败) ——


def test_resume_llm_failure_converges_and_broadcasts() -> None:
    """恢复入口:恢复中 LLM 失败同样收敛,广播 task.failed,响应体 status 如实。"""
    client, _store, emitter = _make_client(
        {
            "order_management": slice_agent([], actions=[PUBLISH]),
            "customer_service": make_agent_runner(_failing_customer_graph()),
        },
        plan=_two_slice_plan(),
    )

    response = _start_and_approve(client)

    assert "task.interrupted" in emitter.names()
    assert "task.failed" in emitter.names(), "恢复中失败须广播终态,不留悬挂"
    assert response.status_code == 200
    assert response.json()["status"] == "failed", "响应体不得在失败恢复上报 completed(与行/事件一致)"
    assert "HTTP 400" in response.json()["error"]


def test_resume_unexpected_error_converges_then_raises() -> None:
    """恢复入口:编程错误 → 行收敛 failed + 广播 task.failed,端点仍 500。"""

    async def scripted(slice_) -> dict:
        if slice_.no == 2:
            raise KeyError("编程错误")
        return {"agent": slice_.agent, "description": slice_.description, "executed": True, "actions": [PUBLISH]}

    client, _store, emitter = _make_client(
        {"order_management": scripted, "customer_service": scripted},
        plan=_two_slice_plan(),
        raise_app_exceptions=False,
    )

    _start_and_approve(client)

    assert "task.failed" in emitter.names()


# —— 接缝 3:图成功后的簿记失败(端点尾部兜底) ——


def test_post_graph_bookkeeping_failure_converges_then_raises() -> None:
    """图跑完后的簿记(记忆落库)失败 → 同样收敛行/广播,端点 500。"""
    client, _store, emitter = _make_client(
        {"order_management": slice_agent([], answer="完成")}, memory=FailingMemory(), raise_app_exceptions=False
    )

    response = client.post("/api/tasks", json={"request": "上架商品"})

    assert response.status_code == 500
    assert "task.failed" in emitter.names()


# —— 任务行收敛(PG 集成;离线秒 skip) ——


async def _seed_user() -> tuple[int, str]:
    async with SessionFactory() as session, session.begin():
        username = f"fail-{uuid.uuid4().hex[:8]}"
        user = User(username=username, password_hash=hash_password("pw"))
        session.add(user)
        await session.flush()
        return user.id, username


def _authed_client(
    agents: dict,
    *,
    plan: SlicePlan | None = None,
    memory=None,
    raise_app_exceptions: bool = True,
) -> AsyncClient:
    store = InMemoryApprovalBatchStore()
    emitter = RecordingEmitter()
    apply_fn = FakeApply()
    graph = build_supervisor(
        StubPlanner(plan),
        agents=agents,
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=apply_fn,
        emitter=emitter,
    )
    return AsyncClient(
        transport=ASGITransport(
            app=create_app(
                graph=graph,
                batch_store=store,
                apply_fn=apply_fn,
                emitter=emitter,
                memory=memory,
                auth_required=True,
            ),
            raise_app_exceptions=raise_app_exceptions,
        ),
        base_url="http://test",
    )


def _require_postgres() -> None:
    if not postgres_reachable(get_settings().database_url):
        pytest.skip("Postgres 离线(compose dev 库),integration 跳过")


@pytest.mark.integration
async def test_task_row_failed_on_llm_failure() -> None:
    """发起入口:LLM 失败 → 201 + 行 failed + result.error 如实落库(无悬挂 in_progress)。"""
    _require_postgres()
    user_id, username = await _seed_user()
    client = _authed_client({"customer_service": make_agent_runner(_failing_customer_graph())}, plan=_customer_plan())
    client.headers.update({"Authorization": f"Bearer {create_token(username, user_id)}"})
    async with client:
        created = await client.post("/api/tasks", json={"request": "回复买家"})
        assert created.status_code == 201
        assert created.json()["status"] == "failed"

        detail = await client.get(f"/api/tasks/{created.json()['thread_id']}")
        assert detail.json()["status"] == "failed"
        assert "HTTP 400" in detail.json()["result"]["error"]


@pytest.mark.integration
async def test_resume_row_failed_with_reason() -> None:
    """恢复入口:切片 2 恢复中失败 → 行 failed(不留 in_progress)+ result.error 含原因。"""
    _require_postgres()
    user_id, username = await _seed_user()
    client = _authed_client(
        {
            "order_management": slice_agent([], actions=[PUBLISH]),
            "customer_service": make_agent_runner(_failing_customer_graph()),
        },
        plan=_two_slice_plan(),
    )
    client.headers.update({"Authorization": f"Bearer {create_token(username, user_id)}"})
    async with client:
        thread_id = (await client.post("/api/tasks", json={"request": "上架并回复"})).json()["thread_id"]
        batch_id = (await client.get(f"/api/threads/{thread_id}/approvals")).json()["approvals"][0]["batchId"]
        resumed = await client.post(
            f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "approve", "comment": ""}}
        )
        assert resumed.status_code == 200

        detail = await client.get(f"/api/tasks/{thread_id}")
        assert detail.json()["status"] == "failed"
        assert "HTTP 400" in detail.json()["result"]["error"]


@pytest.mark.integration
async def test_task_row_failed_on_post_graph_error() -> None:
    """图成功后的簿记失败:行同样收敛 failed(不留 in_progress),端点 500。"""
    _require_postgres()
    user_id, username = await _seed_user()
    session = f"fail-post-graph-{uuid.uuid4().hex[:8]}"
    client = _authed_client(
        {"order_management": slice_agent([], answer="完成")}, memory=FailingMemory(), raise_app_exceptions=False
    )
    client.headers.update({"Authorization": f"Bearer {create_token(username, user_id)}"})
    async with client:
        response = await client.post("/api/tasks", json={"request": "上架商品", "session_id": session})
        assert response.status_code == 500

        listing = await client.get("/api/tasks", params={"session_id": session})
        tasks = listing.json()["tasks"]
        assert len(tasks) == 1 and tasks[0]["status"] == "failed"


@pytest.mark.integration
async def test_task_row_failed_on_unexpected_error() -> None:
    """编程错误:行收敛 failed(端点 500,观测保留)。"""
    _require_postgres()

    async def boom(slice_) -> dict:
        raise KeyError("编程错误")

    user_id, username = await _seed_user()
    session = f"fail-unexpected-{uuid.uuid4().hex[:8]}"
    client = _authed_client({"order_management": boom}, raise_app_exceptions=False)
    client.headers.update({"Authorization": f"Bearer {create_token(username, user_id)}"})
    async with client:
        response = await client.post("/api/tasks", json={"request": "上架商品", "session_id": session})
        assert response.status_code == 500

        listing = await client.get("/api/tasks", params={"session_id": session})
        tasks = listing.json()["tasks"]
        assert len(tasks) == 1 and tasks[0]["status"] == "failed"
