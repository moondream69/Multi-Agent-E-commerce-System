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
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]

    approvals = client.get(f"/api/threads/{thread_id}/approvals").json()["approvals"]

    expected = _ts_interface_fields("ApprovalBatch")
    assert len(approvals) == 1
    assert set(approvals[0]) == expected, f"响应键 {set(approvals[0])} 应等于契约字段 {expected}"
    action = approvals[0]["actions"][0]
    assert action["action"] == "product.publish"
    assert action["params"] == {"product_id": 1}
    assert action["snapshot"] == {"exists": True, "status": "draft"}


async def test_approval_list_envelope_matches_contract() -> None:
    """审批列表信封键 == ts ApprovalListResponse;plans 元素 == ThreadPlan(spec #34)。"""
    expected = _ts_interface_fields("ApprovalListResponse")
    assert expected == {"approvals", "plans"}, f"信封字段 {expected} 应恰为 approvals + plans"
    assert _ts_interface_fields("ThreadPlan") == {"request", "plan"}


def test_approval_batch_status_values_match_contract() -> None:
    """批次六态字符串集合 == ts ApprovalBatchStatus 联合值。"""
    from python_backend.db.models import ApprovalStatus

    backend = {s.value for s in ApprovalStatus}
    contract = _ts_union_values("ApprovalBatchStatus")
    assert backend == contract, f"后端六态 {backend} 应等于契约 {contract}"


def test_ws_event_names_present() -> None:
    """WS 事件名(审批/任务/通知/切片)在契约中保留(spec #9:旧系统事件名已清理)。"""
    text = EVENTS_TS.read_text(encoding="utf-8")
    assert "APPROVAL_REQUESTED: 'approval.requested'" in text
    assert "APPROVAL_DECIDED: 'approval.decided'" in text
    assert "NOTIFICATION_CREATED: 'notification.created'" in text
    assert "NOTIFICATION_READ: 'notification.read'" in text
    assert "TASK_INTERRUPTED: 'task.interrupted'" in text
    # 协作面板数据源:规划/分派轨迹实时可见
    assert "TASK_PLANNED: 'task.planned'" in text
    assert "SLICE_STARTED: 'slice.started'" in text
    assert "SLICE_COMPLETED: 'slice.completed'" in text


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


async def test_conversation_messages_match_contract() -> None:
    """GET /conversations/{id}/messages 响应键 == ts ConversationMessages/ConversationMessage(spec #20)。"""
    from python_backend.core.auth import create_token
    from tests.conftest import InMemorySessionMemory

    memory = InMemorySessionMemory()
    await memory.record("s-contract", 42, role="user", content="契约探针", task_id="t-contract")
    client = TestClient(create_app(auth_required=False, conversation_store=memory))
    client.headers.update({"Authorization": f"Bearer {create_token('tester', 42)}"})

    body = client.get("/api/conversations/s-contract/messages").json()

    assert set(body) == _ts_interface_fields("ConversationMessages")
    assert set(body["conversation"]) == _ts_interface_fields("ConversationMeta"), "元数据复用会话列表形状"
    assert set(body["messages"][0]) == _ts_interface_fields("ConversationMessage")


async def test_product_list_item_matches_contract() -> None:
    """GET /api/products 响应键 == ts ProductListItem(spec #9;spec #34 起经注入替身,离线可跑)。"""
    from decimal import Decimal

    from fastapi.testclient import TestClient

    from python_backend.db.models import Product
    from tests.conftest import InMemoryProductStore

    store = InMemoryProductStore()
    store.products.append(
        Product(
            id=1,
            sku="CONTRACT-1",
            title="契约商品",
            price=Decimal("1.00"),
            currency="USD",
            category="测试",
            stock=1,
            alert_threshold=10,
        )
    )
    client = TestClient(create_app(auth_required=False, product_store=store))
    products = client.get("/api/products").json()["products"]
    expected = _ts_interface_fields("ProductListItem")
    assert products and set(products[0]) == expected, f"响应键 {set(products[0])} 应等于契约字段 {expected}"


async def test_order_list_item_matches_contract() -> None:
    """GET /api/orders 信封键 == ts OrderListResponse;元素 == OrderListItem(spec #34)。"""
    from decimal import Decimal

    from fastapi.testclient import TestClient

    from python_backend.db.models import Order, OrderStatus
    from tests.conftest import InMemoryOrderStore

    store = InMemoryOrderStore()
    store.orders.append(
        Order(
            id=1,
            reference="CONTRACT-ORD",
            product_id=1,
            status=OrderStatus.PENDING,
            total_amount=Decimal("10.00"),
            currency="USD",
            fx_rate=Decimal("7.1234"),
        )
    )
    client = TestClient(create_app(auth_required=False, order_store=store))
    body = client.get("/api/orders").json()

    assert set(body) == _ts_interface_fields("OrderListResponse"), "信封键 == 契约字段"
    assert set(body["orders"][0]) == _ts_interface_fields("OrderListItem"), "元素键 == 契约字段"


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


async def test_task_detail_result_matches_contract() -> None:
    """任务详情字段 == ts TaskDetail;result.summary 为字符串(issue #25:契约声明 string,不得回对象)。

    三面收口同链验证(离线,经 create_app 注入缝):详情响应 / POST 响应 / 助手消息落库。
    """
    from python_backend.core.auth import create_token
    from tests.conftest import InMemorySessionMemory

    memory = InMemorySessionMemory()
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent()},  # 无审批动作 → 免审直行 → completed
        checkpointer=InMemorySaver(),
    )
    client = TestClient(create_app(graph=graph, task_store=InMemoryTaskStore(), memory=memory, auth_required=False))
    client.headers.update({"Authorization": f"Bearer {create_token('tester', 42)}"})

    created = client.post("/api/tasks", json={"request": "查库存"}).json()
    detail = client.get(f"/api/tasks/{created['threadId']}").json()

    assert created["summary"] == "完成 1/1 个切片", "POST 响应摘要为字符串(issue #25)"
    assert set(detail) == _ts_interface_fields("TaskDetail"), "详情响应键 == 契约字段"
    assert detail["status"] == "completed"
    assert set(detail["result"]) == {"summary", "error"}, "result 形状 == 契约内联声明 {summary?, error?}"
    assert detail["result"]["summary"] == "完成 1/1 个切片", "摘要 = 「完成 M/N 个切片」(不得回对象)"
    assert [r["content"] for r in memory.records if r["role"] == "assistant"] == ["完成 1/1 个切片"], (
        "助手消息落库为可读文本(issue #25:不再是 Python repr)"
    )


async def test_create_task_response_keys_match_contract() -> None:
    """POST /api/tasks 响应键 == ts TaskCreated(issue #26:键名口径收口,顶层响应键全驼峰)。

    终态键集恰为契约字段(四键);interrupted 早返回分支形状保持二键(⊆ 契约字段)。
    """
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent()},  # 无审批动作 → 免审直行 → completed
        checkpointer=InMemorySaver(),
    )
    client = TestClient(create_app(graph=graph, task_store=InMemoryTaskStore(), auth_required=False))
    created = client.post("/api/tasks", json={"request": "查库存"}).json()

    expected = _ts_interface_fields("TaskCreated")
    assert set(created) == expected, f"POST 响应键 {set(created)} 应等于契约字段 {expected}"
    assert created["threadId"], "threadId 非空(前端 setSelected 的直接消费值)"

    batch_store = InMemoryApprovalBatchStore()
    interrupted_graph = build_supervisor(
        StubPlanner(),  # 默认计划含审批切片 → interrupt
        agents={"order_management": slice_agent(actions=[{"action": "product.publish", "params": {"product_id": 1}}])},
        checkpointer=InMemorySaver(),
        batch_store=batch_store,
    )
    interrupted_client = TestClient(
        create_app(
            graph=interrupted_graph, batch_store=batch_store, task_store=InMemoryTaskStore(), auth_required=False
        )
    )
    interrupted = interrupted_client.post("/api/tasks", json={"request": "上架商品"}).json()

    assert set(interrupted) == {"threadId", "status"}, "interrupted 分支形状保持二键"
    assert set(interrupted) <= expected, "interrupted 键集 ⊆ 契约字段(声明名必须是契约名)"
    assert interrupted["status"] == "interrupted"


async def test_drafting_response_matches_contract() -> None:
    """POST /api/drafting 响应键 == ts DraftingResponse 接口字段(spec #8 B11)。"""
    from fastapi.testclient import TestClient

    from python_backend.core.drafting import DraftingService
    from tests.conftest import FakeLlm

    class StubMentions:
        """商品指代查证替身(issue #39):契约用例离线可跑,不触 PG。"""

        async def find_mentions(self, message: str, *, limit: int) -> tuple[list[dict], bool]:
            return [], False

    client = TestClient(
        create_app(
            auth_required=False,
            drafting=DraftingService(llm=FakeLlm(responses=["草稿"]), product_mentions=StubMentions()),
        )
    )
    body = client.post("/api/drafting", json={"message": "你好", "locale": "zh"}).json()
    expected = _ts_interface_fields("DraftingResponse")
    assert set(body) == expected, f"响应键 {set(body)} 应等于契约字段 {expected}"
    assert set(body["evidence"]) == _ts_interface_fields("DraftingEvidence")


def test_citation_shape_matches_contract() -> None:
    """引用条目键 == ts Citation / CitationChunk / CitationRecord 字段(issue #51;记录条目 #67)。

    条目两类共用同一载荷(语料切块 / 系统记录):这里两种都造一条,键各自对齐 ts 接口。
    """
    from python_backend.core.citations import build_citations

    hits = [
        {
            "id": "faq-returns#6",
            "score": 0.83,
            "payload": {
                "doc_id": "faq-returns",
                "title": "退款多久到账?",
                "source": "自造 FAQ 语料库",
                "published_at": "2026-09-14",
                "section": "退货退款",
                "chunk_index": 6,
                "content": "Q: 退款多久到账?\nA: 1-3 个工作日。",
            },
        }
    ]
    _text, citations = build_citations("仓库验收后发起退款[faq-returns#6]。", hits)

    assert set(citations[0]) == _ts_interface_fields("CorpusCitation")
    assert set(citations[0]["chunks"][0]) == _ts_interface_fields("CitationChunk")

    record_source = {
        "kind": "product",
        "title": "桌面收纳架 深空黑款",
        "source": "商品库(系统查询结果)",
        "record": {"id": "product:82", "content": "SKU SYN-HM-081 · 库存 2"},
    }
    _text, records = build_citations("该款库存仅剩 2 件[1]。", [record_source], allow_ordinals=True)

    assert set(records[0]) == _ts_interface_fields("RecordCitation")
    assert set(records[0]["record"]) == _ts_interface_fields("CitationRecord")


async def test_login_response_matches_contract() -> None:
    """登录响应键 == ts LoginResponse 接口字段(spec #8 A1)。"""
    expected = _ts_interface_fields("LoginResponse")
    assert expected == {"token", "username"}
