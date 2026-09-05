"""分级审批护栏测试:风险分级 + 守卫执行 + API(integration,需 docker Postgres,模式同 test_reply_templates)。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from python_backend.api.app import create_app
from python_backend.api.auth import create_access_token
from python_backend.core.approval import GuardContext, execute_guarded, register_tool, risk_level
from python_backend.core.event_bus import EventBus
from python_backend.core.orchestrator import Orchestrator
from python_backend.db import approval_repo
from python_backend.db.base import Base
from python_backend.db.models import ApprovalRequest, ApprovalStatus
from python_backend.db.session import SessionLocal, engine
from python_backend.domain.events import AgentEventType
from python_backend.domain.tasks import ToolDefinition
from python_backend.settings import settings

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"Authorization": f"Bearer {create_access_token('tester')}"}


class FakeTool:
    definition = ToolDefinition(name="order_workflow", description="fake", parameters=[])

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, params: dict[str, Any]) -> Any:
        self.calls.append(params)
        return {"ok": True, **params}


@pytest.fixture()
def prepared_db():
    Base.metadata.create_all(engine)  # 幂等:表已存在时无操作
    yield


@pytest.fixture()
def clean_approvals(prepared_db):
    yield
    with SessionLocal() as session:
        session.execute(delete(ApprovalRequest))
        session.commit()


def test_risk_level_mapping():
    assert risk_level("order_workflow", {"action": "transition"}) == "approve"
    assert risk_level("order_workflow", {"action": "create"}) == "auto"
    assert risk_level("product_crud", {"action": "updateStatus"}) == "approve"
    assert risk_level("product_crud", {"action": "create"}) == "auto"
    assert risk_level("faq_search", {}) == "auto"


async def test_auto_executes_directly(clean_approvals):
    tool = FakeTool()
    ctx = GuardContext("t-1", "agent-1", "alice", EventBus())
    result = await execute_guarded(
        "order_workflow",
        {"action": "create", "sku": "X-1"},
        lambda: tool.execute({"action": "create", "sku": "X-1"}),
        ctx,
    )
    assert result["ok"] is True
    assert len(tool.calls) == 1


async def test_approve_blocks_until_decided(clean_approvals):
    tool = FakeTool()
    bus = EventBus()
    requested = asyncio.Event()

    def on_requested(event) -> None:
        requested.set()

    bus.on(AgentEventType.APPROVAL_REQUESTED, on_requested)
    ctx = GuardContext("t-2", "agent-1", "alice", bus)

    task = asyncio.create_task(
        execute_guarded(
            "order_workflow",
            {"action": "transition", "orderId": "o-1", "to": "shipped"},
            lambda: tool.execute({"action": "transition", "orderId": "o-1", "to": "shipped"}),
            ctx,
        )
    )
    await asyncio.wait_for(requested.wait(), timeout=5)
    rows = approval_repo.list_requests("pending")
    assert len(rows) == 1
    row = rows[0]
    assert row.toolName == "order_workflow"
    assert row.params["orderId"] == "o-1"

    approval_repo.decide_request(str(row.id), True, "bob", None)
    result = await asyncio.wait_for(task, timeout=5)
    assert result["ok"] is True
    assert len(tool.calls) == 1


async def test_rejected_returns_error_text(clean_approvals):
    tool = FakeTool()
    ctx = GuardContext("t-3", "agent-1", "alice", EventBus())
    task = asyncio.create_task(
        execute_guarded(
            "order_workflow",
            {"action": "transition", "orderId": "o-1", "to": "shipped"},
            lambda: tool.execute({"action": "transition", "orderId": "o-1", "to": "shipped"}),
            ctx,
        )
    )
    await asyncio.sleep(0.6)  # 等守卫插行(轮询间隔 1.5s 内)
    rows = approval_repo.list_requests("pending")
    assert len(rows) == 1
    approval_repo.decide_request(str(rows[0].id), False, "bob", "金额异常")
    result = await asyncio.wait_for(task, timeout=5)
    assert "拒绝" in result["error"]
    assert "金额异常" in result["error"]
    assert len(tool.calls) == 0


async def test_expired_returns_timeout(clean_approvals):
    tool = FakeTool()
    ctx = GuardContext("t-4", "agent-1", "alice", EventBus())
    task = asyncio.create_task(
        execute_guarded(
            "order_workflow",
            {"action": "transition", "orderId": "o-1", "to": "shipped"},
            lambda: tool.execute({"action": "transition", "orderId": "o-1", "to": "shipped"}),
            ctx,
        )
    )
    await asyncio.sleep(0.6)
    rows = approval_repo.list_requests("pending")
    approval_repo.expire_one(str(rows[0].id))
    result = await asyncio.wait_for(task, timeout=5)
    assert "超时" in result["error"]
    assert len(tool.calls) == 0


async def test_shadow_mode_records_without_executing(clean_approvals, monkeypatch):
    monkeypatch.setattr(settings, "shadow_mode", True)
    tool = FakeTool()
    ctx = GuardContext("t-5", "agent-1", "alice", EventBus())
    result = await execute_guarded(
        "order_workflow",
        {"action": "transition", "orderId": "o-1", "to": "shipped"},
        lambda: tool.execute({"action": "transition", "orderId": "o-1", "to": "shipped"}),
        ctx,
    )
    assert "影子" in result["warning"]
    assert len(tool.calls) == 0
    rows = approval_repo.list_requests("shadow")
    assert len(rows) == 1
    assert rows[0].mode == "shadow"


async def test_shadow_execute_endpoint(clean_approvals, monkeypatch):
    monkeypatch.setattr(settings, "shadow_mode", True)
    tool = FakeTool()
    register_tool(tool)
    ctx = GuardContext("t-6", "agent-1", "alice", EventBus())
    await execute_guarded(
        "order_workflow",
        {"action": "transition", "orderId": "o-1", "to": "shipped"},
        lambda: tool.execute({"action": "transition", "orderId": "o-1", "to": "shipped"}),
        ctx,
    )
    row = approval_repo.list_requests("shadow")[0]
    client = TestClient(create_app(Orchestrator(EventBus())), headers=AUTH_HEADERS)
    res = client.post(f"/api/approvals/{row.id}/execute")
    assert res.status_code == 200
    assert len(tool.calls) == 1
    executed = approval_repo.get_request(str(row.id))
    assert executed is not None
    assert executed.status == ApprovalStatus.EXECUTED


async def test_decide_endpoint(clean_approvals):
    request_id = approval_repo.create_request(
        "order_workflow", {"action": "transition", "orderId": "o-1"}, "agent-1", "t-1", "alice", "approval"
    )
    client = TestClient(create_app(Orchestrator(EventBus())), headers=AUTH_HEADERS)
    res = client.post(f"/api/approvals/{request_id}/decide", json={"approve": False, "comment": "测试拒绝"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "rejected"
    assert data["decidedBy"] == "tester"
    assert data["comment"] == "测试拒绝"
    # 二次决定(重复操作)应 404
    again = client.post(f"/api/approvals/{request_id}/decide", json={"approve": True})
    assert again.status_code == 404
