"""订单存储缝(spec #34):OrderStore 内存替身语义 + GET /api/orders 流程(离线,不触 PG)。

替身复现生产可见语义:状态筛选、created_at 倒序(同刻插入倒排 = 生产 id 次序 tie-breaker)、
分页切片 + 同筛选总数;日快照按日取当日最后一笔、缺汇率行跳过、无单日不产出点。
SQL 侧行为(DISTINCT ON / date_trunc / 窗口表达式)不复刻,其正确性以集成测试为准绳。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from python_backend.api.app import create_app
from python_backend.db.models import Order, OrderStatus
from tests.conftest import InMemoryOrderStore

NOW = datetime.now(UTC)


def _order(**overrides) -> Order:
    base = {
        "id": 1,
        "reference": "ORD-1",
        "product_id": 1,
        "customer_id": None,
        "status": OrderStatus.PENDING,
        "total_amount": Decimal("100.00"),
        "currency": "USD",
        "fx_rate": Decimal("7.1234"),
        "fx_base_currency": "CNY",
        "platform": "amazon",
        "created_at": NOW - timedelta(days=1),
    }
    return Order(**{**base, **overrides})


# —— 内存替身可见语义 ——


async def test_list_orders_filters_paginates_and_counts_by_difference() -> None:
    """筛选/分页/总数:total 是**同筛选**行数(不是全表),倒序最新在前,翻页不重不漏。"""
    store = InMemoryOrderStore()
    store.orders.extend(
        [
            _order(id=1, status=OrderStatus.PENDING, created_at=NOW - timedelta(days=3)),
            _order(id=2, status=OrderStatus.SHIPPED, created_at=NOW - timedelta(days=2)),
            _order(id=3, status=OrderStatus.PENDING, created_at=NOW - timedelta(days=1)),
        ]
    )

    page, total = await store.list_orders(status=None, limit=2, offset=0)
    assert [row["id"] for row in page] == [3, 2] and total == 3, "全量:倒序 + 全表总数"

    page, total = await store.list_orders(status="pending", limit=10, offset=0)
    assert [row["id"] for row in page] == [3, 1] and total == 2, "筛选后总数按筛选口径"

    page, total = await store.list_orders(status="pending", limit=1, offset=1)
    assert [row["id"] for row in page] == [1] and total == 2, "翻页取第二页,总数不随页变"


async def test_list_orders_same_instant_falls_back_to_insertion_order() -> None:
    """同刻多单:后插入者在前(对应生产 created_at desc, id desc 的稳定次序)。"""
    store = InMemoryOrderStore()
    store.orders.extend([_order(id=1, created_at=NOW), _order(id=2, created_at=NOW)])

    page, _ = await store.list_orders(status=None, limit=10, offset=0)

    assert [row["id"] for row in page] == [2, 1]


async def test_order_payload_shape_carries_snapshot_and_null_rate() -> None:
    """载荷形状:汇率快照以字符串落载荷、缺快照为 null(数据台显「待核」的判据)。"""
    store = InMemoryOrderStore()
    store.orders.extend([_order(id=1), _order(id=2, fx_rate=None, reference=None)])

    page, _ = await store.list_orders(status=None, limit=10, offset=0)
    with_rate = next(row for row in page if row["id"] == 1)
    without_rate = next(row for row in page if row["id"] == 2)

    assert set(with_rate) == {
        "id",
        "reference",
        "productId",
        "customerId",
        "status",
        "totalAmount",
        "currency",
        "fxRate",
        "fxBaseCurrency",
        "platform",
        "createdAt",
    }
    assert with_rate["fxRate"] == "7.1234" and with_rate["reference"] == "ORD-1"
    assert without_rate["fxRate"] is None and without_rate["reference"] is None


async def test_daily_fx_snapshots_takes_last_of_day_and_skips_gaps() -> None:
    """日快照:按日取当日最后一笔、缺汇率行跳过、无单日不产出、窗外不产出、日期升序。"""
    # 固定正午锚点:同日两笔须始终落在同一 UTC 日——直接用 NOW 减小时数会在 UTC 钟点 < 06:00
    # 时跨 UTC 零点分成两天,断言随运行时刻漂移(daily_fx_snapshots 按 UTC 日聚合)
    day3 = NOW.replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=3)
    store = InMemoryOrderStore()
    store.orders.extend(
        [
            _order(id=1, fx_rate=Decimal("7.1000"), created_at=day3 - timedelta(hours=6)),  # 同日更早
            _order(id=2, fx_rate=Decimal("7.2000"), created_at=day3),  # 同日更晚:胜出
            _order(id=3, fx_rate=None, created_at=day3 + timedelta(days=1)),  # 缺汇率:跳过
            _order(id=4, fx_rate=Decimal("7.3000"), created_at=day3 + timedelta(days=2)),
            _order(id=5, fx_rate=Decimal("6.9000"), created_at=day3 - timedelta(days=27)),  # 窗外:不计
        ]
    )

    points = await store.daily_fx_snapshots(days=7, currency="USD")

    assert [point["rate"] for point in points] == ["7.2000", "7.3000"], "同日取末笔;缺汇率日不产出点"
    assert points == sorted(points, key=lambda point: point["date"]), "日期升序(前端折线左→右)"
    assert len(points) == 2, "无单日与窗外日均无点(断点由前端画,不造中间值)"


# —— 端点流程(注入替身) ——


def test_orders_endpoint_serves_injected_store_with_envelope() -> None:
    """GET /api/orders 返回 {orders, total}(离线反证:探针行只在替身里)。"""
    store = InMemoryOrderStore()
    store.orders.append(_order(id=9, reference="ORD-PROBE"))

    response = TestClient(create_app(auth_required=False, order_store=store)).get("/api/orders")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"orders", "total"}
    assert [row["reference"] for row in body["orders"]] == ["ORD-PROBE"] and body["total"] == 1


def test_orders_endpoint_rejects_invalid_params() -> None:
    """参数边界:状态非法 / limit 越界 / offset 负 一律 422(不静默截断)。"""
    client = TestClient(create_app(auth_required=False, order_store=InMemoryOrderStore()))

    assert client.get("/api/orders?status=teleported").status_code == 422
    assert client.get("/api/orders?limit=0").status_code == 422
    assert client.get("/api/orders?limit=201").status_code == 422
    assert client.get("/api/orders?offset=-1").status_code == 422
    assert client.get("/api/orders?status=pending&limit=50&offset=0").status_code == 200
