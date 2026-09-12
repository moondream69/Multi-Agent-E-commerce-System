"""汇率卡片数据面(spec #34):GET /api/fx 端点流程(离线,不触 PG / 不外呼)。

组合两面:当期汇率经 FxQuoteProvider(缓存优先 → API,不可用抛 FxUnavailableError)、
走势经 OrderStore.daily_fx_snapshots(订单快照按日聚合)。两条路的替身/桩都在本文件注入。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from python_backend.api.app import create_app
from python_backend.db.models import Order, OrderStatus
from python_backend.infrastructure.fx import FxQuote, FxUnavailableError
from tests.conftest import InMemoryOrderStore

NOW = datetime.now(UTC)


class StubQuoteProvider:
    """脚本化报价桩:quote 为 None 时抛 FxUnavailableError(模拟 API 与缓存双失效)。"""

    def __init__(self, quote: FxQuote | None) -> None:
        self.quote = quote
        self.calls = 0

    async def get_quote_cny(self, currency: str) -> FxQuote:
        self.calls += 1
        if self.quote is None:
            raise FxUnavailableError(f"汇率 API 失效且无缓存(币种 {currency})")
        return self.quote


def _order(**overrides) -> Order:
    base = {
        "id": 1,
        "reference": "FX-1",
        "product_id": 1,
        "status": OrderStatus.PENDING,
        "total_amount": Decimal("100.00"),
        "currency": "USD",
        "fx_rate": Decimal("7.1234"),
        "created_at": NOW - timedelta(days=1),
    }
    return Order(**{**base, **overrides})


def _app(quote: FxQuote | None, *, orders: list[Order] | None = None) -> tuple[TestClient, StubQuoteProvider]:
    provider = StubQuoteProvider(quote)
    store = InMemoryOrderStore()
    store.orders.extend(orders or [])
    return TestClient(create_app(auth_required=False, fx_service=provider, order_store=store)), provider


def test_fx_card_returns_quote_with_cache_time_and_trend() -> None:
    """当期值 + 缓存时刻 + 来源 + 近 7 日走势(来自订单快照,升序)。"""
    cached_at = datetime(2026, 9, 12, 8, 30, tzinfo=UTC)
    client, provider = _app(
        FxQuote(rate=Decimal("7.12340000"), cached_at=cached_at, source="cache"),
        orders=[
            _order(id=1, fx_rate=Decimal("7.1000"), created_at=NOW - timedelta(days=2)),
            _order(id=2, fx_rate=Decimal("7.2000"), created_at=NOW - timedelta(days=1)),
        ],
    )

    body = client.get("/api/fx").json()

    assert set(body) == {"base", "currency", "rate", "cachedAt", "source", "trend"}
    assert body["base"] == "CNY" and body["currency"] == "USD"
    assert body["rate"] == "7.12340000" and body["cachedAt"] == cached_at.isoformat() and body["source"] == "cache"
    assert body["trend"]["windowDays"] == 7
    assert [point["rate"] for point in body["trend"]["points"]] == ["7.1000", "7.2000"]
    assert set(body["trend"]["points"][0]) == {"date", "rate"}
    assert provider.calls == 1


def test_fx_card_degrades_to_pending_review_without_quote() -> None:
    """双失效:rate / cachedAt / source 置空(前端显「待核」),走势与信封仍在 —— 不 500。"""
    client, _ = _app(None, orders=[_order(id=1, fx_rate=Decimal("7.1500"))])

    response = client.get("/api/fx")

    assert response.status_code == 200
    body = response.json()
    assert body["rate"] is None and body["cachedAt"] is None and body["source"] is None
    assert [point["rate"] for point in body["trend"]["points"]] == ["7.1500"], "汇率面不可用不影响成交走势"


def test_fx_card_trend_is_empty_when_no_snapshots() -> None:
    """无成交快照:点集为空(前端显「暂无成交参考」),不造中间值。"""
    client, _ = _app(FxQuote(rate=Decimal("7.12340000"), cached_at=None, source="live"))

    body = client.get("/api/fx").json()

    assert body["trend"]["points"] == []
    assert body["cachedAt"] is None, "旧缓存无伴生键:时刻如实为 null"


def test_fx_card_trend_ignores_other_currencies_and_missing_rates() -> None:
    """走势只取本卡币种且有快照的行:混币种/缺汇率单不污染折线。"""
    client, _ = _app(
        FxQuote(rate=Decimal("7.12340000"), cached_at=None, source="live"),
        orders=[
            _order(id=1, fx_rate=Decimal("7.1000"), created_at=NOW - timedelta(days=1)),
            _order(id=2, currency="EUR", fx_rate=Decimal("7.9000"), created_at=NOW - timedelta(days=1)),
            _order(id=3, fx_rate=None, created_at=NOW - timedelta(days=1)),
        ],
    )

    points = client.get("/api/fx").json()["trend"]["points"]

    assert [point["rate"] for point in points] == ["7.1000"]
