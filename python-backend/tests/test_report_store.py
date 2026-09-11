"""报表存储缝(issue #21):ReportStore 内存替身语义 + GET /api/reports/summary 流程(离线,不触 PG)。

替身复现生产聚合口径:状态分组计数、窗内 Σ(金额 x 汇率) 量化 2 位、缺汇率单计 unconverted、
低库存同谓词(stock < 阈值,触线不告警)、未结工单计数——SQL 侧行为(表达式/类型转换)不复刻,
聚合正确性证明以 test_reports.py(integration)为准绳;本文件保证端点契约与替身语义可离线验证。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from python_backend.api.app import create_app
from python_backend.db.models import Order, OrderStatus, Product, Ticket, TicketStatus
from tests.conftest import InMemoryReportStore

NOW = datetime.now(UTC)


def _product(**overrides) -> Product:
    base = {
        "id": 1,
        "sku": "RPT-1",
        "title": "报表商品",
        "price": Decimal("10.00"),
        "category": "测试",
        "stock": 50,
        "alert_threshold": 10,
    }
    return Product(**{**base, **overrides})


def _order(**overrides) -> Order:
    base = {
        "id": 1,
        "product_id": 1,
        "status": OrderStatus.PENDING,
        "total_amount": Decimal("100.00"),
        "currency": "USD",
        "fx_rate": Decimal("7.1234"),
        "created_at": NOW - timedelta(days=1),
    }
    return Order(**{**base, **overrides})


# —— 内存替身可见语义(与生产同口径) ——


async def test_aggregates_status_revenue_and_unconverted_by_difference() -> None:
    """套用集成用例同款「前后差值」断言:窗内计入、窗外/缺汇率按口径分离。"""
    store = InMemoryReportStore()
    before = await store.build_summary()

    store.orders.extend(
        [
            _order(id=1),  # 窗内带汇率:100.00 x 7.1234 = 712.34
            _order(id=2, total_amount=Decimal("50.00"), fx_rate=None),  # 缺汇率:只计 unconverted
            _order(id=3, status=OrderStatus.SHIPPED, created_at=NOW - timedelta(days=10)),  # 窗外:不计金额
        ]
    )
    after = await store.build_summary()

    assert after["orders"]["total"] - before["orders"]["total"] == 3
    assert after["orders"]["byStatus"].get("pending", 0) - before["orders"]["byStatus"].get("pending", 0) == 2
    assert Decimal(after["revenue"]["amount"]) - Decimal(before["revenue"]["amount"]) == Decimal("712.34")
    assert after["revenue"]["unconverted"] - before["revenue"]["unconverted"] == 1
    assert after["revenue"]["windowDays"] == 7 and after["revenue"]["baseCurrency"] == "CNY"
    assert after["revenue"]["amount"].count(".") == 1, "2 位小数字符串(与契约同形)"


async def test_low_stock_uses_same_predicate_and_open_tickets_counted() -> None:
    """低库存 = stock < alert_threshold 且按 stock 升序;触线不告警;未结工单计数、已结不计。"""
    store = InMemoryReportStore()
    store.products.extend(
        [
            _product(id=1, stock=2),
            _product(id=2, sku="RPT-EQ", stock=10),  # 触线:与库存告警同谓词,不告警
        ]
    )
    store.tickets.extend(
        [
            # status 显式给:SQLAlchemy 列 default 只在 flush 时生效,替身入参须自带(下同)
            Ticket(id=1, message="未结", created_by="t", status=TicketStatus.OPEN, created_at=NOW),
        ]
    )

    summary = await store.build_summary()

    assert [item["productId"] for item in summary["lowStock"]] == [1]
    low = summary["lowStock"][0]
    assert set(low) == {"productId", "sku", "title", "stock", "alertThreshold"}
    assert low["stock"] == 2 and low["alertThreshold"] == 10
    assert summary["tickets"]["open"] == 1


# —— 端点流程(注入替身) ——


async def test_empty_store_returns_zero_shaped_contract() -> None:
    """空行集:总量 0、成交额 "0.00"(量化后 2 位字符串)、unconverted 0,不因无数据抛错。"""
    summary = await InMemoryReportStore().build_summary()

    assert summary["orders"] == {"byStatus": {}, "total": 0}
    assert summary["revenue"]["amount"] == "0.00"
    assert summary["revenue"]["unconverted"] == 0
    assert summary["lowStock"] == [] and summary["tickets"] == {"open": 0}


async def test_report_endpoint_returns_contract_shape() -> None:
    """GET /api/reports/summary 经注入替身返回契约信封(离线即可断言端点接线与键形状)。"""
    store = InMemoryReportStore()
    store.products.append(_product(id=1, stock=1))
    store.orders.append(_order(id=1))
    response = TestClient(create_app(auth_required=False, report_store=store)).get("/api/reports/summary")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"orders", "revenue", "lowStock", "tickets"}
    assert body["orders"] == {"byStatus": {"pending": 1}, "total": 1}
    assert body["revenue"]["amount"] == "712.34"
    assert [item["productId"] for item in body["lowStock"]] == [1]
    assert body["tickets"] == {"open": 0}
