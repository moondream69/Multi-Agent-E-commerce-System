"""B9/B10 integration(spec #8 Testing):真 Postgres 上下单扣库存 + 汇率快照 + 审批 apply 路径。

- REST 下单:扣减落库、负数防护(库存不足 409)、金额非法 404、汇率不可用 409
- 并发:两单同商品(库存 1)→ 一成一败,无超卖(行锁)
- order.create(审批动作):批次批准后 apply 扣减落库;库存不足/汇率不可用整批不执行
依赖 compose dev Postgres;离线时 TCP 探测秒 skip。
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from python_backend.agents.executor import apply_batch_actions
from python_backend.api.app import create_app
from python_backend.core.approvals import classify_action
from python_backend.db.approval_store import PostgresApprovalBatchStore
from python_backend.db.models import Order, Product, ProductStatus
from python_backend.db.session import SessionFactory
from python_backend.infrastructure.fx import FxUnavailableError
from tests.conftest import RecordingEmitter

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("requires_postgres")]


class FakeFxService:
    """脚本化汇率服务(成功/失效二态),计数断言调用。"""

    def __init__(self, rate: str = "7.20000000", *, fail: bool = False) -> None:
        self.rate = Decimal(rate)
        self.fail = fail
        self.calls = 0

    async def get_rate_cny(self, currency: str) -> Decimal:
        self.calls += 1
        if self.fail:
            raise FxUnavailableError("汇率 API 失效且无缓存(测试)")
        if currency.upper() == "CNY":
            return Decimal(1)
        return self.rate


async def _make_product(stock: int = 5, *, alert_threshold: int = 1) -> Product:
    """测试商品:阈值默认 1(即默认不触发库存告警,告警断言显式传阈值)。"""
    async with SessionFactory() as session:
        product = Product(
            sku=f"SKU-{uuid.uuid4().hex[:8]}",
            title="下单测试品",
            price=Decimal("9.99"),
            category="测试",
            stock=stock,
            status=ProductStatus.ACTIVE,
            alert_threshold=alert_threshold,
        )
        session.add(product)
        await session.commit()
        return product


async def _order_count(product_id: int) -> int:
    async with SessionFactory() as session:
        return (
            await session.execute(select(func.count()).select_from(Order).where(Order.product_id == product_id))
        ).scalar_one()


async def _stock_of(product_id: int) -> int:
    async with SessionFactory() as session:
        product = await session.get(Product, product_id)
        assert product is not None
        return product.stock


def _client(fx: FakeFxService, *, emitter: RecordingEmitter | None = None) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app(fx_service=fx, emitter=emitter, auth_required=False)),
        base_url="http://test",
    )


async def test_rest_order_deducts_stock_and_snapshots_fx() -> None:
    """B9+B10:REST 下单扣真实库存,订单落库含汇率快照(基准 CNY)。"""
    fx = FakeFxService()
    product = await _make_product(stock=5)
    async with _client(fx) as client:
        response = await client.post(
            "/api/orders",
            json={"product_id": product.id, "total_amount": 19.98, "currency": "USD"},
        )
    assert response.status_code == 201
    order = response.json()["order"]
    assert order["product_id"] == product.id
    assert order["fx_rate"] == "7.20000000"
    assert order["fx_base_currency"] == "CNY"
    assert await _stock_of(product.id) == 4


async def test_rest_order_insufficient_stock_409() -> None:
    """负数防护:库存 0 下单被拒 409,不产生订单、库存不变。"""
    fx = FakeFxService()
    product = await _make_product(stock=0)
    async with _client(fx) as client:
        response = await client.post("/api/orders", json={"product_id": product.id, "total_amount": 9.99})
    assert response.status_code == 409
    assert await _stock_of(product.id) == 0
    assert await _order_count(product.id) == 0


async def test_rest_order_invalid_amount_404() -> None:
    """金额非法(≤0):如实报错,不扣库存。"""
    fx = FakeFxService()
    product = await _make_product(stock=5)
    async with _client(fx) as client:
        response = await client.post("/api/orders", json={"product_id": product.id, "total_amount": -1})
    assert response.status_code == 404
    assert await _stock_of(product.id) == 5


async def test_rest_order_fx_unavailable_leaves_blank_and_notifies() -> None:
    """B10 演进(spec #9):汇率 API 失效且缓存为空 → 照常落单,fx_rate 留空 + fx_missing 通知。

    增量 5 的「409 拒单」暂态已到期升级(留空 + 人工可见),不再拒单。
    """
    fx = FakeFxService(fail=True)
    emitter = RecordingEmitter()
    product = await _make_product(stock=5)
    async with _client(fx, emitter=emitter) as client:
        response = await client.post("/api/orders", json={"product_id": product.id, "total_amount": 9.99})
    assert response.status_code == 201
    order = response.json()["order"]
    assert order["fx_rate"] is None
    assert order["fx_base_currency"] == "CNY"
    assert await _stock_of(product.id) == 4
    assert await _order_count(product.id) == 1
    kinds = [payload["kind"] for _event, payload in emitter.events]
    assert "fx_missing" in kinds  # 人工可见:汇率缺失通知
    assert "order_status" in kinds  # 订单创建通知


async def test_rest_order_notifies_created_status() -> None:
    """A8:下单成功后广播 order_status 通知(状态映射表文案,零 LLM)。"""
    fx = FakeFxService()
    emitter = RecordingEmitter()
    product = await _make_product(stock=5)
    async with _client(fx, emitter=emitter) as client:
        response = await client.post("/api/orders", json={"product_id": product.id, "total_amount": 9.99})
    assert response.status_code == 201
    assert [event for event, _payload in emitter.events] == ["notification.created"]
    payload = emitter.events[0][1]
    assert payload["kind"] == "order_status"
    assert "已提交" in payload["message"]
    assert payload["orderId"] == response.json()["order"]["id"]


async def test_rest_order_triggers_inventory_alert_below_product_threshold() -> None:
    """A9:扣减后低于商品自身阈值 → 五档库存告警通知(阈值按商品,非全局)。"""
    fx = FakeFxService()
    emitter = RecordingEmitter()
    product = await _make_product(stock=2, alert_threshold=10)
    async with _client(fx, emitter=emitter) as client:
        response = await client.post("/api/orders", json={"product_id": product.id, "total_amount": 9.99})
    assert response.status_code == 201
    kinds = [payload["kind"] for _event, payload in emitter.events]
    assert kinds == ["order_status", "inventory_alert"]
    alert = next(payload for _event, payload in emitter.events if payload["kind"] == "inventory_alert")
    assert "库存严重不足" in alert["message"]  # 1/10 = 0.1 → 严重不足档
    assert alert["orderId"] is None


async def test_rest_order_no_inventory_alert_above_threshold() -> None:
    """库存仍高于商品阈值时不发告警(不噪声)。"""
    fx = FakeFxService()
    emitter = RecordingEmitter()
    product = await _make_product(stock=3, alert_threshold=2)
    async with _client(fx, emitter=emitter) as client:
        response = await client.post("/api/orders", json={"product_id": product.id, "total_amount": 9.99})
    assert response.status_code == 201
    assert [payload["kind"] for _event, payload in emitter.events] == ["order_status"]


async def test_concurrent_orders_no_oversell() -> None:
    """并发两单同商品(库存 1):行锁保证一成一败,库存不为负(B18 前置)。"""
    fx = FakeFxService()
    product = await _make_product(stock=1)
    async with _client(fx) as client:
        first, second = await asyncio.gather(
            client.post("/api/orders", json={"product_id": product.id, "total_amount": 9.99}),
            client.post("/api/orders", json={"product_id": product.id, "total_amount": 9.99}),
        )
    assert {first.status_code, second.status_code} == {201, 409}
    assert await _stock_of(product.id) == 0
    assert await _order_count(product.id) == 1


async def test_order_create_classified_approval() -> None:
    """order.create 分类=审批(LLM 渠道扣真实库存进护栏,必然推论)。"""
    assert classify_action("order.create") == "approval"


async def test_order_create_apply_deducts_stock() -> None:
    """order.create 批准后 apply:事务内扣减 + 订单落库(快照比对只校验存在性)。"""
    fx = FakeFxService()
    product = await _make_product(stock=3)
    store = PostgresApprovalBatchStore()
    batch_id = f"b-{uuid.uuid4().hex[:8]}"
    await store.create_batch(
        batch_id=batch_id,
        thread_id=f"t-{uuid.uuid4().hex[:8]}",
        slice_no=1,
        action_type="order.create",
        actions=[
            {
                "action": "order.create",
                "params": {"product_id": product.id, "total_amount": 9.99, "currency": "USD"},
                "snapshot": {"exists": True, "stock": 3, "title": product.title},
            }
        ],
        mode="approval",
    )
    await store.decide_batch(batch_id=batch_id, decision="approve")
    record = await store.get_batch(batch_id=batch_id)
    assert record is not None
    outcome = await apply_batch_actions(batch_id, [record.actions[0]], fx=fx)
    assert outcome.applied is True
    assert await _stock_of(product.id) == 2
    async with SessionFactory() as session:
        order = (await session.execute(select(Order).where(Order.product_id == product.id))).scalar_one()
        assert order.fx_rate == Decimal("7.20000000")
        assert order.fx_base_currency == "CNY"


async def test_order_create_apply_insufficient_stock_batch_fails() -> None:
    """apply 时库存不足:整批不执行(applied=False),库存与订单不变。"""
    fx = FakeFxService()
    product = await _make_product(stock=0)
    store = PostgresApprovalBatchStore()
    batch_id = f"b-{uuid.uuid4().hex[:8]}"
    await store.create_batch(
        batch_id=batch_id,
        thread_id=f"t-{uuid.uuid4().hex[:8]}",
        slice_no=1,
        action_type="order.create",
        actions=[
            {
                "action": "order.create",
                "params": {"product_id": product.id, "total_amount": 9.99, "currency": "USD"},
                "snapshot": {"exists": True, "stock": 0, "title": product.title},
            }
        ],
        mode="approval",
    )
    await store.decide_batch(batch_id=batch_id, decision="approve")
    record = await store.get_batch(batch_id=batch_id)
    assert record is not None
    outcome = await apply_batch_actions(batch_id, [record.actions[0]], fx=fx)
    assert outcome.applied is False
    assert "库存不足" in (outcome.reason or "")
    assert await _order_count(product.id) == 0


async def test_order_create_apply_stock_drift_conflict() -> None:
    """apply 时库存与快照漂移:B18 冲突整批不执行(审批意图与现状不符,如实上报)。"""
    fx = FakeFxService()
    product = await _make_product(stock=2)  # 实际库存已从快照时的 3 变为 2
    store = PostgresApprovalBatchStore()
    batch_id = f"b-{uuid.uuid4().hex[:8]}"
    await store.create_batch(
        batch_id=batch_id,
        thread_id=f"t-{uuid.uuid4().hex[:8]}",
        slice_no=1,
        action_type="order.create",
        actions=[
            {
                "action": "order.create",
                "params": {"product_id": product.id, "total_amount": 9.99, "currency": "USD"},
                "snapshot": {"exists": True, "stock": 3, "title": product.title},
            }
        ],
        mode="approval",
    )
    await store.decide_batch(batch_id=batch_id, decision="approve")
    record = await store.get_batch(batch_id=batch_id)
    assert record is not None
    outcome = await apply_batch_actions(batch_id, [record.actions[0]], fx=fx)
    assert outcome.applied is False
    assert "库存已变化" in (outcome.reason or "")
    assert await _stock_of(product.id) == 2  # 未扣减
    assert await _order_count(product.id) == 0  # 未落单


async def test_order_create_apply_fx_unavailable_leaves_blank() -> None:
    """apply 时汇率不可用(spec #9):留空落单 + fx_missing 效果,不再整批不执行。"""
    fx = FakeFxService(fail=True)
    product = await _make_product(stock=3)
    store = PostgresApprovalBatchStore()
    batch_id = f"b-{uuid.uuid4().hex[:8]}"
    await store.create_batch(
        batch_id=batch_id,
        thread_id=f"t-{uuid.uuid4().hex[:8]}",
        slice_no=1,
        action_type="order.create",
        actions=[
            {
                "action": "order.create",
                "params": {"product_id": product.id, "total_amount": 9.99, "currency": "USD"},
                "snapshot": {"exists": True, "stock": 3, "title": product.title},
            }
        ],
        mode="approval",
    )
    await store.decide_batch(batch_id=batch_id, decision="approve")
    record = await store.get_batch(batch_id=batch_id)
    assert record is not None
    outcome = await apply_batch_actions(batch_id, [record.actions[0]], fx=fx)
    assert outcome.applied is True
    assert {effect["type"] for effect in outcome.effects} == {"order_status", "fx_missing"}
    assert await _stock_of(product.id) == 2
    async with SessionFactory() as session:
        order = (await session.execute(select(Order).where(Order.product_id == product.id))).scalar_one()
        assert order.fx_rate is None
        assert order.fx_base_currency == "CNY"
