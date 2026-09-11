"""买家存储缝(issue #21):CustomerStore 内存替身语义 + GET /api/customers 流程(离线,不触 PG)。

替身复现生产可见语义:列表创建倒序、seed 仅 dev 且按 email 幂等;
端点路由经 create_app 注入替身(探针买家可见即反证)。真 PG 的 seed 幂等/.env 剖面见
test_customers.py(integration)。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from python_backend.api.app import create_app
from tests.conftest import InMemoryCustomerStore

PROBE_BUYERS = [{"name": "探针买家", "email": "probe@example.com", "locale": "zh-CN"}]


class _Settings:
    """环境剖面桩(替身只读 environment 属性,与 settings.Settings 同名字段)。"""

    def __init__(self, environment: str) -> None:
        self.environment = environment


def _client(store: InMemoryCustomerStore) -> TestClient:
    return TestClient(create_app(auth_required=False, customer_store=store))


# —— 内存替身可见语义(与生产同口径) ——


async def test_list_newest_first_and_payload_shape() -> None:
    """列表覆盖全部买家、最新在前;信封只含契约四键(不泄漏 created_at 等内部列)。"""
    store = InMemoryCustomerStore()
    store.add("先建买家", "first@example.com")
    store.add("后建买家", "second@example.com", locale="en-US")

    rows = await store.list_customers()

    assert [row["name"] for row in rows] == ["后建买家", "先建买家"]
    assert set(rows[0]) == {"customerId", "name", "email", "locale"}
    assert rows[0]["locale"] == "en-US"


async def test_seed_dev_only_and_idempotent() -> None:
    """seed 仅 dev 生效;dev 下按 email 幂等(两次调用只落一行,不覆盖既有行)。"""
    prod = InMemoryCustomerStore(settings_provider=lambda: _Settings("prod"), demo_buyers=PROBE_BUYERS)
    await prod.ensure_demo_buyers()
    assert prod.rows == [], "非 dev:不 seed"

    dev = InMemoryCustomerStore(settings_provider=lambda: _Settings("dev"), demo_buyers=PROBE_BUYERS)
    await dev.ensure_demo_buyers()
    await dev.ensure_demo_buyers()
    assert [row.email for row in dev.rows] == ["probe@example.com"], "两次调用只落一行"

    dev.rows[0].name = "被改过的名字"
    await dev.ensure_demo_buyers()
    assert dev.rows[0].name == "被改过的名字", "幂等判定按 email,既有行不被覆盖"


async def test_environment_switch_after_first_seed_skips() -> None:
    """环境剖面逐次读取:同实例先 dev 后 prod,后一次调用不落任何行(生产不落模拟数据)。"""
    profile = _Settings("dev")
    store = InMemoryCustomerStore(settings_provider=lambda: profile, demo_buyers=PROBE_BUYERS)

    await store.ensure_demo_buyers()
    assert len(store.rows) == 1

    profile.environment = "prod"
    await store.ensure_demo_buyers()
    assert len(store.rows) == 1, "切到 prod:seed 不再发生(即便原本会新增也不落)"


# —— 端点流程(注入替身) ——


async def test_customers_endpoint_reads_injected_store() -> None:
    """GET 返回替身行集(探针买家在响应里 → 反证端点经 app.state.customer_store 分发)。"""
    store = InMemoryCustomerStore()
    store.add("端点探针买家", "endpoint-probe@example.com")
    store.add("后建买家", "later@example.com")

    response = _client(store).get("/api/customers")

    assert response.status_code == 200
    customers = response.json()["customers"]
    assert [row["email"] for row in customers] == ["later@example.com", "endpoint-probe@example.com"]
    assert set(customers[0]) == {"customerId", "name", "email", "locale"}
