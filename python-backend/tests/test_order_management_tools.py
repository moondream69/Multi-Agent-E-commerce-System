"""list_orders / list_approvals 只读查询工具单测(需 docker Postgres)。"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy import Table
from sqlalchemy.orm import Session

from python_backend.agents.order_management.agent import OrderManagementAgent
from python_backend.agents.order_management.tools import (
    AnomalyDetectionTool,
    ApprovalListTool,
    InventoryAlertTool,
    OrderListTool,
    OrderWorkflowTool,
    ProductCrudTool,
)
from python_backend.core.event_bus import EventBus
from python_backend.db import approval_repo
from python_backend.db.base import Base
from python_backend.db.models import ApprovalRequest, Order, OrderStatus, Product
from python_backend.db.session import engine
from python_backend.infrastructure.llm import LlmService

pytestmark = pytest.mark.integration


@pytest.fixture()
def prepared_db():
    Base.metadata.create_all(engine)  # 幂等:表已存在时无操作
    yield


@pytest.fixture()
def clean_rows(prepared_db):
    orders: list[str] = []
    products: list[str] = []
    approvals: list[str] = []
    yield orders, products, approvals
    with Session(engine) as session:
        for table, ids in ((Order, orders), (Product, products), (ApprovalRequest, approvals)):
            if ids:
                t = cast(Table, table.__table__)
                session.execute(t.delete().where(t.c.id.in_(ids)))
        session.commit()


def _seed_product() -> str:
    with Session(engine) as session:
        product = Product(sku=f"sku-{uuid.uuid4().hex[:8]}", title="测试商品", price=99.9, category="测试品类")
        session.add(product)
        session.commit()
        session.refresh(product)
        return str(product.id)


def _seed_order(product_id: str, status: OrderStatus) -> str:
    with Session(engine) as session:
        order = Order(
            product_id=product_id,
            totalAmount=Decimal("99.90"),
            status=status,
        )
        session.add(order)
        session.commit()
        session.refresh(order)
        return str(order.id)


# —— definition 契约 ——


def test_list_orders_definition() -> None:
    definition = OrderListTool().definition
    assert definition.name == "list_orders"
    (status_param,) = [p for p in definition.parameters if p.name == "status"]
    assert status_param.required is False
    assert "pending|confirmed|processing|shipped|delivered|cancelled|returned" in definition.description
    assert "pending|confirmed|processing|shipped|delivered|cancelled|returned" in status_param.description


def test_list_approvals_definition() -> None:
    definition = ApprovalListTool().definition
    assert definition.name == "list_approvals"
    (status_param,) = [p for p in definition.parameters if p.name == "status"]
    assert status_param.required is False
    assert "pending|approved|rejected|expired|shadow|executed" in definition.description
    assert "pending|approved|rejected|expired|shadow|executed" in status_param.description


# —— list_orders ——


async def test_list_orders_all_and_by_status(clean_rows):
    product_id = _seed_product()
    clean_rows[1].append(product_id)
    first = _seed_order(product_id, OrderStatus.PENDING)
    second = _seed_order(product_id, OrderStatus.SHIPPED)
    clean_rows[0].extend([first, second])

    tool = OrderListTool()

    all_orders = await tool.execute({})
    all_ids = [o["id"] for o in all_orders]
    assert all_ids.index(second) < all_ids.index(first)  # createdAt desc
    assert all_orders[0]["product"] is not None  # 内嵌商品对象

    shipped_ids = [o["id"] for o in await tool.execute({"status": "shipped"})]
    assert second in shipped_ids and first not in shipped_ids

    pending_ids = [o["id"] for o in await tool.execute({"status": "pending"})]
    assert first in pending_ids and second not in pending_ids

    with pytest.raises(ValueError):
        await tool.execute({"status": "bogus"})


# —— list_approvals ——


async def test_list_approvals_all_and_by_status(clean_rows):
    pending_id = approval_repo.create_request(
        "order_workflow.transition",
        {"orderId": "x", "newStatus": "confirmed"},
        agent_id="order-management",
        task_id="t-1",
        requested_by="tester",
        mode="approval",
    )
    approved_id = approval_repo.create_request(
        "order_workflow.transition",
        {"orderId": "y", "newStatus": "shipped"},
        agent_id="order-management",
        task_id="t-2",
        requested_by="tester",
        mode="approval",
    )
    approval_repo.decide_request(approved_id, approve=True, decided_by="tester", comment=None)
    clean_rows[2].extend([pending_id, approved_id])

    tool = ApprovalListTool()

    all_approvals = await tool.execute({})
    all_ids = [a["id"] for a in all_approvals]
    assert pending_id in all_ids and approved_id in all_ids
    by_id = {a["id"]: a for a in all_approvals}
    assert by_id[pending_id]["status"] == "pending"
    assert by_id[approved_id]["status"] == "approved"
    assert by_id[pending_id]["toolName"] == "order_workflow.transition"
    assert by_id[pending_id]["params"] == {"orderId": "x", "newStatus": "confirmed"}
    assert by_id[pending_id]["mode"] == "approval"

    pending_ids = [a["id"] for a in await tool.execute({"status": "pending"})]
    assert pending_id in pending_ids and approved_id not in pending_ids

    with pytest.raises(ValueError):
        await tool.execute({"status": "bogus"})


# —— Agent 注册面 ——


async def test_order_agent_registers_query_tools_and_prompt_whitelist() -> None:
    agent = OrderManagementAgent(
        EventBus(),
        LlmService(),
        ProductCrudTool(),
        OrderWorkflowTool(),
        InventoryAlertTool(),
        AnomalyDetectionTool(),
        OrderListTool(),
        ApprovalListTool(),
    )
    assert len(agent.get_tools()) == 6
    assert {t.name for t in agent.get_tools()} == {
        "product_crud",
        "order_workflow",
        "check_inventory",
        "detect_anomalies",
        "list_orders",
        "list_approvals",
    }
    assert "pending|confirmed|processing|shipped|delivered|cancelled|returned" in agent.system_prompt
    assert "pending|approved|rejected|expired|shadow|executed" in agent.system_prompt
