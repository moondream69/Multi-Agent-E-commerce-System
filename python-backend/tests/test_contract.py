"""契约测试(仓库契约纪律,CLAUDE.md):frontend/src/types/events.ts 是 API 契约唯一真源。

后端审批批次序列化对照 ts 声明断言:响应键 == 接口字段名(驼峰),
状态值集合 == ApprovalBatchStatus 联合值,审批事件名保留。
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from python_backend.api.app import create_app
from python_backend.core.graph import build_supervisor
from tests.conftest import InMemoryApprovalBatchStore, StubPlanner

REPO_ROOT = Path(__file__).resolve().parents[2]
EVENTS_TS = REPO_ROOT / "frontend" / "src" / "types" / "events.ts"


def _ts_interface_fields(name: str) -> set[str]:
    """从 events.ts 提取接口字段名(契约真源)。"""
    text = EVENTS_TS.read_text(encoding="utf-8")
    match = re.search(rf"export interface {name} \{{(.*?)\n\}}", text, re.DOTALL)
    assert match, f"events.ts 缺少接口 {name}"
    return set(re.findall(r"\n  (\w+)\??:", match.group(1)))


def _ts_union_values(name: str) -> set[str]:
    text = EVENTS_TS.read_text(encoding="utf-8")
    match = re.search(rf"export type {name} = ?(.*?);", text, re.DOTALL)
    assert match, f"events.ts 缺少类型 {name}"
    return set(re.findall(r"'(\w+)'", match.group(1)))


async def test_approval_batch_response_keys_match_contract() -> None:
    """GET /approvals 响应键 == ts ApprovalBatch 接口字段(驼峰)。"""
    store = InMemoryApprovalBatchStore()
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": lambda s: {"executed": True}},
        checkpointer=InMemorySaver(),
        batch_store=store,
    )
    client = TestClient(create_app(graph=graph, batch_store=store))
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["thread_id"]

    approvals = client.get(f"/api/threads/{thread_id}/approvals").json()["approvals"]

    expected = _ts_interface_fields("ApprovalBatch")
    assert len(approvals) == 1
    assert set(approvals[0]) == expected, f"响应键 {set(approvals[0])} 应等于契约字段 {expected}"


def test_approval_batch_status_values_match_contract() -> None:
    """批次六态字符串集合 == ts ApprovalBatchStatus 联合值。"""
    from python_backend.db.models import ApprovalStatus

    backend = {s.value for s in ApprovalStatus}
    contract = _ts_union_values("ApprovalBatchStatus")
    assert backend == contract, f"后端六态 {backend} 应等于契约 {contract}"


def test_approval_event_names_present() -> None:
    """审批事件名(approval.requested / approval.decided)在契约中保留。"""
    text = EVENTS_TS.read_text(encoding="utf-8")
    assert "APPROVAL_REQUESTED: 'approval.requested'" in text
    assert "APPROVAL_DECIDED: 'approval.decided'" in text
