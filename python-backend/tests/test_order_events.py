"""OrderWorkflowTool 事件 emit 单测:create/transition 发出 ORDER_STATUS_CHANGED,校验失败不发出(需 docker Postgres)。"""

import uuid
from typing import cast

import pytest
from sqlalchemy import Table
from sqlalchemy.orm import Session

from python_backend.agents.order_management.tools import OrderWorkflowTool
from python_backend.core.event_bus import EventBus
from python_backend.db.base import Base
from python_backend.db.models import Order, Product
from python_backend.db.session import engine
from python_backend.domain.events import AgentEventType

pytestmark = pytest.mark.integration





@pytest.fixture()
def prepared_db():
    Base.metadata.create_all(engine)  # 幂等:表已存在时无操作
    yield


@pytest.fixture()
def clean_rows(prepared_db):
    orders: list[str] = []
    products: list[str] = []
    yield orders, products
    with Session(engine) as session:
        for table, ids in ((Order, orders), (Product, products)):
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


def _tool(bus: EventBus | None = None) -> OrderWorkflowTool:
    return OrderWorkflowTool(bus)


async def test_create_emits_order_status_changed(clean_rows):
    product_id = _seed_product()
    clean_rows[1].append(product_id)

    captured: list = []
    bus = EventBus()
    bus.on(AgentEventType.ORDER_STATUS_CHANGED, lambda event: captured.append(event))

    result = await _tool(bus).execute({"action": "create", "productId": product_id, "totalAmount": 99.9})
    clean_rows[0].append(result["id"])

    assert len(captured) == 1
    payload = captured[0].payload
    assert payload["orderId"] == result["id"]
    assert payload["from"] is None
    assert payload["to"] == "pending"
    assert payload["productId"] == product_id
    assert payload["totalAmount"] == 99.9


async def test_transition_emits_from_and_to(clean_rows):
    product_id = _seed_product()
    clean_rows[1].append(product_id)

    captured: list = []
    bus = EventBus()
    bus.on(AgentEventType.ORDER_STATUS_CHANGED, lambda event: captured.append(event))
    tool = _tool(bus)

    created = await tool.execute({"action": "create", "productId": product_id, "totalAmount": 99.9})
    clean_rows[0].append(created["id"])
    await tool.execute({"action": "transition", "orderId": created["id"], "newStatus": "confirmed"})

    assert len(captured) == 2
    payload = captured[1].payload
    assert payload["orderId"] == created["id"]
    assert payload["from"] == "pending"
    assert payload["to"] == "confirmed"


async def test_invalid_transition_does_not_emit(clean_rows):
    product_id = _seed_product()
    clean_rows[1].append(product_id)

    captured: list = []
    bus = EventBus()
    bus.on(AgentEventType.ORDER_STATUS_CHANGED, lambda event: captured.append(event))
    tool = _tool(bus)

    created = await tool.execute({"action": "create", "productId": product_id, "totalAmount": 99.9})
    clean_rows[0].append(created["id"])

    with pytest.raises(ValueError, match="状态不可从"):
        await tool.execute({"action": "transition", "orderId": created["id"], "newStatus": "delivered"})
    assert len(captured) == 1  # 只有 create 的事件


async def test_default_construct_without_bus(clean_rows):
    product_id = _seed_product()
    clean_rows[1].append(product_id)

    result = await _tool().execute({"action": "create", "productId": product_id, "totalAmount": 10})
    clean_rows[0].append(result["id"])
    assert result["status"] == "pending"
