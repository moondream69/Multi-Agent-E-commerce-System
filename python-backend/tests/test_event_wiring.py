"""事件接线测试:工具 emit 业务事件、Agent handle_event 实际动作(自动草稿 / 主动通知)。"""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy import Table
from sqlalchemy.orm import Session

from python_backend.agents.customer_service.agent import CustomerServiceAgent
from python_backend.agents.customer_service.tools import (
    EscalateTicketTool,
    FaqRetrievalTool,
    OrderLookupTool,
    SentimentAnalysisTool,
    TemplateManagerTool,
    TranslatorTool,
)
from python_backend.agents.order_management.agent import OrderManagementAgent
from python_backend.agents.order_management.tools import (
    AnomalyDetectionTool,
    InventoryAlertTool,
    OrderWorkflowTool,
    ProductCrudTool,
)
from python_backend.agents.product_research.tools import ReportGeneratorTool
from python_backend.core.event_bus import EventBus, new_event
from python_backend.db.base import Base
from python_backend.db.models import Product, ReplyTemplate
from python_backend.db.session import engine
from python_backend.domain.events import AgentEventType


class FakeLlm:
    """complete() 返回设定的 JSON;可配置抛错以触发降级。"""

    def __init__(self, response: str | None = None, *, raise_error: bool = False) -> None:
        self._response = response
        self._raise = raise_error

    def complete(self, messages: list[dict], **kwargs: Any) -> str:
        if self._raise:
            raise RuntimeError("llm unavailable")
        assert self._response is not None
        return self._response


@pytest.fixture()
def prepared_db():
    Base.metadata.create_all(engine)
    yield


@pytest.fixture()
def clean_rows(prepared_db):
    orders: list[str] = []
    products: list[str] = []
    templates: list[str] = []
    yield orders, products, templates
    with Session(engine) as session:
        for table, ids in ((Product, products), (ReplyTemplate, templates)):
            if ids:
                t = cast(Table, table.__table__)
                session.execute(t.delete().where(t.c.id.in_(ids)))
        session.commit()


def _make_bus() -> tuple[EventBus, list]:
    bus = EventBus()
    captured: list = []
    return bus, captured


def _capture(bus: EventBus, captured: list, type_: AgentEventType) -> None:
    bus.on(type_, lambda event: captured.append(event))


# —— 工具 emit ——


async def test_report_generator_emits():
    bus, captured = _make_bus()
    _capture(bus, captured, AgentEventType.REPORT_GENERATED)
    tool = ReportGeneratorTool(bus)
    result = await tool.execute({"title": "咖啡机报告", "sections": [{"title": "趋势", "content": "内容"}]})
    assert "咖啡机报告" in result
    assert len(captured) == 1
    assert captured[0].payload["title"] == "咖啡机报告"
    assert captured[0].source == "product-research"


@pytest.mark.integration
async def test_product_crud_emits_created(clean_rows):
    bus, captured = _make_bus()
    _capture(bus, captured, AgentEventType.PRODUCT_CREATED)
    tool = ProductCrudTool(bus)
    result = await tool.execute(
        {"action": "create", "sku": f"sku-{uuid.uuid4().hex[:8]}", "title": "测试商品", "price": 10, "category": "测试"}
    )
    clean_rows[1].append(result["id"])
    assert len(captured) == 1
    assert captured[0].payload["product"]["id"] == result["id"]
    assert captured[0].source == "order-management"


@pytest.mark.integration
async def test_product_crud_emits_updated(clean_rows):
    bus, captured = _make_bus()
    _capture(bus, captured, AgentEventType.PRODUCT_UPDATED)
    tool = ProductCrudTool(bus)
    created = await tool.execute(
        {"action": "create", "sku": f"sku-{uuid.uuid4().hex[:8]}", "title": "测试商品", "price": 10, "category": "测试"}
    )
    clean_rows[1].append(created["id"])
    await tool.execute({"action": "updateStatus", "id": created["id"], "status": "active"})
    assert len(captured) == 1
    assert captured[0].payload == {"productId": created["id"], "status": "active"}


async def test_inventory_alert_emits_only_when_alert():
    bus, captured = _make_bus()
    _capture(bus, captured, AgentEventType.INVENTORY_ALERT)
    tool = InventoryAlertTool(bus)

    alerting = await tool.execute({"productName": "咖啡机", "currentStock": 10, "threshold": 100})
    healthy = await tool.execute({"productName": "咖啡机", "currentStock": 500, "threshold": 100})

    assert alerting["alert"] is True
    assert healthy["alert"] is False
    assert len(captured) == 1
    assert captured[0].payload["productName"] == "咖啡机"
    assert captured[0].payload["currentStock"] == 10
    assert captured[0].source == "order-management"


@pytest.mark.integration
async def test_template_manager_emits_on_fill(prepared_db, clean_rows):
    with Session(engine) as session:
        session.add(
            ReplyTemplate(
                id="test_emit", scenario="测试场景", template="您好 {name}", locale="zh-CN", variables=["name"]
            )
        )
        session.commit()
    clean_rows[2].append("test_emit")

    bus, captured = _make_bus()
    _capture(bus, captured, AgentEventType.REPLY_GENERATED)
    tool = TemplateManagerTool(bus)
    result = await tool.execute(
        {"action": "fill", "scenario": "测试场景", "locale": "zh-CN", "variables": {"name": "张伟"}}
    )
    assert result == "您好 张伟"
    assert len(captured) == 1
    assert captured[0].payload == {"scenario": "测试场景", "templateId": "test_emit"}


async def test_escalate_ticket_emits():
    bus, captured = _make_bus()
    _capture(bus, captured, AgentEventType.ESCALATION_TRIGGERED)
    tool = EscalateTicketTool(bus)
    result = await tool.execute({"orderId": "abc", "reason": "客户投诉"})
    assert result["success"] is True
    assert len(captured) == 1
    assert captured[0].payload == {"orderId": "abc", "reason": "客户投诉"}
    assert captured[0].source == "customer-service"


# —— Agent handle_event ——


def _service_agent(bus: EventBus, llm: object) -> CustomerServiceAgent:
    return CustomerServiceAgent(
        bus,
        llm,  # type: ignore
        TranslatorTool(llm),  # type: ignore
        FaqRetrievalTool(),
        SentimentAnalysisTool(llm),  # type: ignore
        TemplateManagerTool(),
        OrderLookupTool(),
        EscalateTicketTool(),
    )


def _order_agent(bus: EventBus, llm: object) -> OrderManagementAgent:
    return OrderManagementAgent(
        bus,
        llm,  # type: ignore
        ProductCrudTool(bus),
        OrderWorkflowTool(),
        InventoryAlertTool(),
        AnomalyDetectionTool(),
    )


@pytest.mark.integration
async def test_order_agent_creates_draft_from_report(clean_rows):
    _ = clean_rows[0]
    bus, captured = _make_bus()
    _capture(bus, captured, AgentEventType.PRODUCT_CREATED)
    llm = FakeLlm(
        '{"sku": "auto-coffee", "title": "便携咖啡机", "price": 59.9,'
        ' "category": "咖啡机", "description": "自动提炼"}'
    )
    agent = _order_agent(bus, llm)

    await agent.handle_event(
        new_event(AgentEventType.REPORT_GENERATED, {"title": "咖啡机报告", "report": "# 报告"}),
    )

    assert len(captured) == 1
    product = captured[0].payload["product"]
    assert product["sku"] == "auto-coffee"
    assert product["title"] == "便携咖啡机"
    assert product["status"] == "draft"
    clean_rows[1].append(product["id"])


@pytest.mark.integration
async def test_order_agent_draft_fallback_on_llm_failure(clean_rows):
    _ = clean_rows[0]
    bus, captured = _make_bus()
    _capture(bus, captured, AgentEventType.PRODUCT_CREATED)
    llm = FakeLlm(None, raise_error=True)
    agent = _order_agent(bus, llm)

    await agent.handle_event(new_event(AgentEventType.REPORT_GENERATED, {"title": "降级标题"}))

    assert len(captured) == 1
    product = captured[0].payload["product"]
    assert product["title"] == "降级标题"
    assert product["price"] == 59.9
    clean_rows[1].append(product["id"])


async def test_service_agent_notifies_on_order_status_change():
    bus, captured = _make_bus()
    _capture(bus, captured, AgentEventType.CUSTOMER_NOTIFICATION)
    agent = _service_agent(bus, FakeLlm(None))

    await agent.handle_event(
        new_event(AgentEventType.ORDER_STATUS_CHANGED, {"orderId": "ord-1", "from": "pending", "to": "shipped"})
    )

    assert len(captured) == 1
    payload = captured[0].payload
    assert payload["agentId"] == "customer-service"
    assert payload["orderId"] == "ord-1"
    assert "ord-1" in payload["message"]
    assert "发货" in payload["message"]
    assert captured[0].type == AgentEventType.CUSTOMER_NOTIFICATION


async def test_service_agent_forwards_inventory_alert():
    bus, captured = _make_bus()
    _capture(bus, captured, AgentEventType.CUSTOMER_NOTIFICATION)
    agent = _service_agent(bus, FakeLlm(None))

    await agent.handle_event(
        new_event(AgentEventType.INVENTORY_ALERT, {"productName": "咖啡机", "message": "咖啡机库存不足!"})
    )

    assert len(captured) == 1
    assert captured[0].payload["message"] == "咖啡机库存不足!"
    assert captured[0].payload["agentId"] == "customer-service"


async def test_service_agent_skips_unknown_order_status():
    bus, captured = _make_bus()
    agent = _service_agent(bus, FakeLlm(None))

    await agent.handle_event(
        new_event(AgentEventType.ORDER_STATUS_CHANGED, {"orderId": "ord-9", "from": "x", "to": "unknown"})
    )
    assert len(captured) == 0
