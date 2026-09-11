"""买家入口集成测试(spec #11):PostgresCustomerStore 的真库行为 + 端点接线。

- 列表:id/name/email/locale,created_at 倒序(种子行直接落库,端点经注入的 PG 实现读回)
- seed:仅 dev;按 email 幂等(两次调用只落一行,生产唯一约束兜底)
离线替身语义见 test_customer_store.py(issue #21);此处保留真 SQL 面。依赖真 PG;离线秒 skip。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from python_backend.api.app import create_app
from python_backend.db import customer_store
from python_backend.db.customer_store import PostgresCustomerStore
from python_backend.db.models import Customer
from python_backend.db.session import SessionFactory

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("requires_postgres")]


async def test_customers_endpoint_shape_and_order() -> None:
    """GET /api/customers:只读列表含 id/name/email/locale,created_at 倒序。

    注入 PG 实现后端点只见种子行——端点若绕过注入直查全表,本断言即破。
    """
    tag = uuid.uuid4().hex[:8]
    store = PostgresCustomerStore()
    async with SessionFactory() as session, session.begin():
        session.add(Customer(name="列表买家", email=f"list-{tag}@example.com", locale="zh-CN"))
    async with AsyncClient(
        transport=ASGITransport(app=create_app(auth_required=False, customer_store=store)), base_url="http://test"
    ) as client:
        response = await client.get("/api/customers")
    assert response.status_code == 200
    customers = response.json()["customers"]
    assert customers and customers[0]["email"] == f"list-{tag}@example.com"  # 最新在前
    assert set(customers[0]) == {"customerId", "name", "email", "locale"}


async def test_demo_buyers_seed_idempotent_dev_only(monkeypatch) -> None:
    """seed:非 dev 不落行;dev 下幂等(两次调用只落一行)。唯一邮箱探针免跨轮串扰。"""
    tag = uuid.uuid4().hex[:8]
    probe = [{"name": "探针买家", "email": f"probe-{tag}@example.com", "locale": "zh-CN"}]
    monkeypatch.setattr(customer_store, "DEMO_BUYERS", probe)

    class ProdSettings:
        environment = "prod"

    monkeypatch.setattr(customer_store, "get_settings", lambda: ProdSettings())
    await PostgresCustomerStore().ensure_demo_buyers()
    async with SessionFactory() as session:
        row = (
            await session.execute(select(Customer.id).where(Customer.email == probe[0]["email"]))
        ).scalar_one_or_none()
    assert row is None  # 非 dev:不 seed

    class DevSettings:
        environment = "dev"

    monkeypatch.setattr(customer_store, "get_settings", lambda: DevSettings())
    store = PostgresCustomerStore()
    await store.ensure_demo_buyers()
    await store.ensure_demo_buyers()
    async with SessionFactory() as session:
        rows = (await session.execute(select(Customer).where(Customer.email == probe[0]["email"]))).scalars().all()
    assert len(rows) == 1  # 幂等:两次调用只落一行
