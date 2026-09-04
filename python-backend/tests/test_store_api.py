"""买家前台 store 路由测试:REST 契约 + 下单落库 + 事件 emit(需 docker Postgres,模式同 test_reply_templates)。"""

import uuid
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from python_backend.api.app import create_app
from python_backend.core.event_bus import EventBus
from python_backend.core.orchestrator import Orchestrator
from python_backend.db.base import Base
from python_backend.db.models import Customer, Order, Product
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
    customers: list[str] = []
    yield orders, products, customers
    with Session(engine) as session:
        # 外键序:先删订单,再删商品与客户
        for table, ids in ((Order, orders), (Product, products), (Customer, customers)):
            if ids:
                t = cast(Table, table.__table__)
                session.execute(t.delete().where(t.c.id.in_(ids)))
        session.commit()


def _seed_product(
    title: str = "测试商品",
    category: str = "测试品类",
    price: float = 99.9,
    status: str = "active",
) -> str:
    with Session(engine) as session:
        product = Product(
            sku=f"sku-{uuid.uuid4().hex[:8]}",
            title=title,
            price=price,
            category=category,
            status=status,
        )
        session.add(product)
        session.commit()
        session.refresh(product)
        return str(product.id)


def _seed_demo_buyer() -> tuple[str, bool]:
    """返回演示买家 (id, 本测试是否新插入):按 store 的固定 email 查找,已有(seed)则复用且 teardown 不清理。"""
    with Session(engine) as session:
        existing = session.scalar(select(Customer.id).where(Customer.email == "zhangwei@example.com"))
        if existing:
            return str(existing), False
        customer = Customer(name="张伟", email="zhangwei@example.com", locale="zh-CN")
        session.add(customer)
        session.commit()
        session.refresh(customer)
        return str(customer.id), True


def _client() -> TestClient:
    return TestClient(create_app(Orchestrator(EventBus())))


def test_list_products_only_active(clean_rows):
    active_id = _seed_product()
    draft_id = _seed_product(title="草稿商品", status="draft")
    clean_rows[1].extend([active_id, draft_id])

    data = _client().get("/api/products").json()
    ids = [p["id"] for p in data]
    assert active_id in ids
    assert draft_id not in ids
    assert set(data[0].keys()) == {
        "id",
        "sku",
        "title",
        "description",
        "price",
        "category",
        "currency",
        "platform",
        "status",
        "createdAt",
        "updatedAt",
    }


def test_list_products_by_category(clean_rows):
    coffee = _seed_product(category="咖啡机")
    earphone = _seed_product(category="耳机")
    clean_rows[1].extend([coffee, earphone])

    data = _client().get("/api/products", params={"category": "咖啡机"}).json()
    assert [p["id"] for p in data] == [coffee]


def test_get_product_by_id(clean_rows):
    product_id = _seed_product()
    clean_rows[1].append(product_id)

    client = _client()
    assert client.get(f"/api/products/{product_id}").json()["title"] == "测试商品"
    assert client.get("/api/products/not-a-uuid").status_code == 400
    assert client.get(f"/api/products/{uuid.uuid4()}").status_code == 404


def test_create_order_defaults_amount_and_binds_demo_buyer(clean_rows):
    product_id = _seed_product(price=59.9)
    buyer_id, inserted = _seed_demo_buyer()
    clean_rows[1].append(product_id)
    if inserted:
        clean_rows[2].append(buyer_id)

    client = _client()
    response = client.post("/api/orders", json={"productId": product_id})
    assert response.status_code == 200
    order = response.json()
    assert order["status"] == "pending"
    assert order["totalAmount"] == 59.9
    assert order["customerId"] == buyer_id
    assert order["product"]["id"] == product_id
    clean_rows[0].append(order["id"])

    with Session(engine) as session:
        row = session.get(Order, uuid.UUID(order["id"]))
        assert row is not None
        assert row.status.value == "pending"
        assert float(row.totalAmount) == 59.9


def test_create_order_missing_product_404(clean_rows):
    client = _client()
    assert client.post("/api/orders", json={"productId": str(uuid.uuid4())}).status_code == 404
    assert client.post("/api/orders", json={"productId": "not-a-uuid"}).status_code == 400


def test_create_order_emits_order_status_changed(clean_rows):
    product_id = _seed_product()
    clean_rows[1].append(product_id)

    bus = EventBus()
    captured: list = []
    bus.on(AgentEventType.ORDER_STATUS_CHANGED, lambda event: captured.append(event))
    client = TestClient(create_app(Orchestrator(bus)))

    order = client.post("/api/orders", json={"productId": product_id}).json()
    clean_rows[0].append(order["id"])

    assert len(captured) == 1
    payload = captured[0].payload
    assert payload["orderId"] == order["id"]
    assert payload["from"] is None
    assert payload["to"] == "pending"
    assert payload["productId"] == product_id
    assert payload["totalAmount"] == order["totalAmount"]


def test_list_orders_with_nested_product(clean_rows):
    product_id = _seed_product()
    clean_rows[1].append(product_id)

    client = _client()
    order = client.post("/api/orders", json={"productId": product_id}).json()
    clean_rows[0].append(order["id"])

    data = client.get("/api/orders").json()
    assert data[0]["id"] == order["id"]
    assert data[0]["product"]["id"] == product_id
    assert data[0]["productId"] == product_id
    assert data[0]["status"] == "pending"
