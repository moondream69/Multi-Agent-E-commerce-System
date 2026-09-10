"""买家入口测试(spec #11):GET /api/customers 只读查询 + 演练 seed(dev-only 幂等)。

- 列表:id/name/email/locale,created_at 倒序(模拟流量买家池 + 运营查询)
- seed:仅 dev;按 email 幂等;非 dev 环境不落任何行
依赖真 PG;离线秒 skip。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from python_backend.api.app import create_app
from python_backend.db import customer_store
from python_backend.db.customer_store import ensure_demo_buyers
from python_backend.db.models import Customer
from python_backend.db.session import SessionFactory
from python_backend.settings import get_settings
from tests.conftest import postgres_reachable

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _require_postgres() -> None:
    if not postgres_reachable(get_settings().database_url):
        pytest.skip("Postgres 离线(compose dev 库),integration 跳过")


async def test_customers_endpoint_shape_and_order() -> None:
    """GET /api/customers:只读列表含 id/name/email/locale,created_at 倒序。"""
    tag = uuid.uuid4().hex[:8]
    async with SessionFactory() as session, session.begin():
        session.add(Customer(name="列表买家", email=f"list-{tag}@example.com", locale="zh-CN"))
    async with AsyncClient(
        transport=ASGITransport(app=create_app(auth_required=False)), base_url="http://test"
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
    await ensure_demo_buyers()
    async with SessionFactory() as session:
        row = (
            await session.execute(select(Customer.id).where(Customer.email == probe[0]["email"]))
        ).scalar_one_or_none()
    assert row is None  # 非 dev:不 seed

    class DevSettings:
        environment = "dev"

    monkeypatch.setattr(customer_store, "get_settings", lambda: DevSettings())
    await ensure_demo_buyers()
    await ensure_demo_buyers()
    async with SessionFactory() as session:
        rows = (await session.execute(select(Customer).where(Customer.email == probe[0]["email"]))).scalars().all()
    assert len(rows) == 1  # 幂等:两次调用只落一行
