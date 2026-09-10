"""契约测试(仓库契约纪律,CLAUDE.md):frontend/src/types/events.ts 是 API 契约唯一真源。

后端审批批次序列化对照 ts 声明断言:响应键 == 接口字段名(驼峰),
状态值集合 == ApprovalBatchStatus 联合值,审批事件名保留。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from python_backend.api.app import create_app
from python_backend.core.graph import build_supervisor
from tests.conftest import InMemoryApprovalBatchStore, InMemoryTaskStore, StubPlanner, slice_agent

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
        agents={
            "order_management": slice_agent(
                actions=[
                    {
                        "action": "product.publish",
                        "params": {"product_id": 1},
                        "snapshot": {"exists": True, "status": "draft"},
                    }
                ]
            )
        },
        checkpointer=InMemorySaver(),
        batch_store=store,
    )
    client = TestClient(create_app(graph=graph, batch_store=store, task_store=InMemoryTaskStore(), auth_required=False))
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["thread_id"]

    approvals = client.get(f"/api/threads/{thread_id}/approvals").json()["approvals"]

    expected = _ts_interface_fields("ApprovalBatch")
    assert len(approvals) == 1
    assert set(approvals[0]) == expected, f"响应键 {set(approvals[0])} 应等于契约字段 {expected}"
    action = approvals[0]["actions"][0]
    assert action["action"] == "product.publish"
    assert action["params"] == {"product_id": 1}
    assert action["snapshot"] == {"exists": True, "status": "draft"}


def test_approval_batch_status_values_match_contract() -> None:
    """批次六态字符串集合 == ts ApprovalBatchStatus 联合值。"""
    from python_backend.db.models import ApprovalStatus

    backend = {s.value for s in ApprovalStatus}
    contract = _ts_union_values("ApprovalBatchStatus")
    assert backend == contract, f"后端六态 {backend} 应等于契约 {contract}"


def test_ws_event_names_present() -> None:
    """WS 事件名(审批/任务/通知)在契约中保留(spec #9:旧系统事件名已清理)。"""
    text = EVENTS_TS.read_text(encoding="utf-8")
    assert "APPROVAL_REQUESTED: 'approval.requested'" in text
    assert "APPROVAL_DECIDED: 'approval.decided'" in text
    assert "NOTIFICATION_CREATED: 'notification.created'" in text
    assert "TASK_INTERRUPTED: 'task.interrupted'" in text


def test_legacy_event_types_removed() -> None:
    """契约清理(spec #9):旧系统死类型不再出现在真源(前后端零引用的 chat:response 等)。"""
    text = EVENTS_TS.read_text(encoding="utf-8")
    for legacy in ("chat:response", "agent:event", "chat:notification", "agent.status_changed", "report.generated"):
        assert legacy not in text, f"旧事件类型 {legacy} 应已清理"


async def test_notification_message_matches_contract() -> None:
    """通知信封字段 == ts NotificationMessage(spec #9 A8/A9/A14)。"""
    from python_backend.core.notifications import build_notifications

    payload = build_notifications([{"type": "order_status", "order_id": 1, "to": "shipped"}])[0]
    expected = _ts_interface_fields("NotificationMessage")
    assert set(payload) == expected, f"通知载荷键 {set(payload)} 应等于契约字段 {expected}"


async def test_notification_feed_matches_contract() -> None:
    """GET /api/notifications 响应键 == ts NotificationFeed;元素 == NotificationMessage(增量 8-T2)。"""
    from python_backend.core.auth import create_token
    from python_backend.core.notifications import build_notifications
    from tests.conftest import InMemoryNotificationStore

    store = InMemoryNotificationStore(user_ids=(42,))
    await store.record(build_notifications([{"type": "order_status", "order_id": 1, "to": "shipped"}]))
    client = TestClient(create_app(auth_required=False, notification_store=store))
    client.headers.update({"Authorization": f"Bearer {create_token('tester', 42)}"})

    body = client.get("/api/notifications").json()

    assert set(body) == _ts_interface_fields("NotificationFeed"), "读响应键 == 契约字段"
    assert set(body["notifications"][0]) == _ts_interface_fields("NotificationMessage"), "落库行与信封同形"


async def test_conversation_meta_matches_contract() -> None:
    """会话列表字段 == ts ConversationMeta(spec #9 A2)。"""
    expected = _ts_interface_fields("ConversationMeta")
    assert expected == {"sessionId", "title", "updatedAt", "messageCount"}


async def test_product_list_item_matches_contract() -> None:
    """GET /api/products 响应键 == ts ProductListItem(spec #9:模拟流量发现商品)。"""
    from fastapi.testclient import TestClient

    from python_backend.settings import get_settings
    from tests.conftest import postgres_reachable

    if not postgres_reachable(get_settings().database_url):
        pytest.skip("Postgres 离线:商品列表形状由序列化函数保证")
    client = TestClient(create_app(auth_required=False))
    products = client.get("/api/products").json()["products"]
    expected = _ts_interface_fields("ProductListItem")
    if not products:
        pytest.skip("测试库无商品:形状由接口序列化保证")
    assert set(products[0]) == expected, f"响应键 {set(products[0])} 应等于契约字段 {expected}"


async def test_actions_metadata_matches_contract() -> None:
    """GET /api/actions 响应键 == ts ActionMeta 接口字段(spec #8 注册表单一化)。"""
    from fastapi.testclient import TestClient

    client = TestClient(create_app(auth_required=False))
    actions = client.get("/api/actions").json()["actions"]
    expected = _ts_interface_fields("ActionMeta")
    assert actions and set(actions[0]) == expected, f"响应键 {set(actions[0])} 应等于契约字段 {expected}"


async def test_task_list_matches_contract() -> None:
    """任务列表契约字段(spec #8 驾驶舱):字段集钉死,防接口意外漂移。

    响应键的真实对照在 test_task_api 集成测试(需 PG 落任务行);此处锁 ts 接口形状。
    """
    expected = _ts_interface_fields("TaskListItem")
    assert expected == {"threadId", "sessionId", "type", "status", "title", "createdAt"}


async def test_drafting_response_matches_contract() -> None:
    """POST /api/drafting 响应键 == ts DraftingResponse 接口字段(spec #8 B11)。"""
    from fastapi.testclient import TestClient

    from python_backend.core.drafting import DraftingService
    from tests.conftest import FakeLlm

    client = TestClient(create_app(auth_required=False, drafting=DraftingService(llm=FakeLlm(responses=["草稿"]))))
    body = client.post("/api/drafting", json={"message": "你好", "locale": "zh"}).json()
    expected = _ts_interface_fields("DraftingResponse")
    assert set(body) == expected, f"响应键 {set(body)} 应等于契约字段 {expected}"
    assert set(body["evidence"]) == _ts_interface_fields("DraftingEvidence")


async def test_login_response_matches_contract() -> None:
    """登录响应键 == ts LoginResponse 接口字段(spec #8 A1)。"""
    expected = _ts_interface_fields("LoginResponse")
    assert expected == {"token", "username"}
