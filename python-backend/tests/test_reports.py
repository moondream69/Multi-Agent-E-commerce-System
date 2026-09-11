"""报表聚合集成测试(spec #11):PostgresReportStore 的纯 SQL 聚合口径。

- 固定数据集 + 前后差值断言(dev 库跨轮累积,绝对值不可断言)
- 成交额:仅窗内且带汇率快照的订单;缺汇率单列 unconverted;窗外不计
- 低库存谓词与库存告警一致:stock < 阈值(触线不告警)
依赖真 PG;离线秒 skip。issue #21:端点契约与替身语义的离线等价用例见 test_report_store.py。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from python_backend.api.app import create_app
from python_backend.db.models import Order, OrderStatus, Product, Ticket, TicketStatus
from python_backend.db.session import SessionFactory

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("requires_postgres")]


async def _summary() -> dict:
    async with AsyncClient(
        transport=ASGITransport(app=create_app(auth_required=False)), base_url="http://test"
    ) as client:
        response = await client.get("/api/reports/summary")
    assert response.status_code == 200
    return response.json()


async def _save(*rows: object) -> None:
    """多行批插:同一 session 一次 add_all(枚举列经 insertmanyvalues;#12 修复前会渲染原生枚举 cast)。"""
    async with SessionFactory() as session, session.begin():
        session.add_all(rows)


def _status_count(summary: dict, status: str) -> int:
    return summary["orders"]["byStatus"].get(status, 0)


async def test_report_summary_aggregates() -> None:
    """经营快照:订单分布/成交额换算(缺汇率单列)/低库存同谓词/未结工单,均按差值断言。"""
    tag = uuid.uuid4().hex[:8]
    before = await _summary()

    product = Product(sku=f"RPT-{tag}", title=f"报表商品-{tag}", price=Decimal("10.00"), category="测试", stock=50)
    low = Product(sku=f"RPT-LOW-{tag}", title=f"低库存-{tag}", price=Decimal("10.00"), category="测试", stock=2)
    equal = Product(sku=f"RPT-EQ-{tag}", title=f"触线不告警-{tag}", price=Decimal("10.00"), category="测试", stock=10)
    await _save(product, low, equal)  # 先落父行供订单外键引用
    await _save(
        # 窗内带汇率:计入成交额(100.00 * 7.1234 = 712.34)
        Order(
            product_id=product.id,
            status=OrderStatus.PENDING,
            total_amount=Decimal("100.00"),
            currency="USD",
            fx_rate=Decimal("7.1234"),
        ),
        # 窗内缺汇率:计入 unconverted,不计金额
        Order(
            product_id=product.id,
            status=OrderStatus.PENDING,
            total_amount=Decimal("50.00"),
            currency="USD",
            fx_rate=None,
        ),
        # 窗外带汇率:窗口过滤,不计
        Order(
            product_id=product.id,
            status=OrderStatus.SHIPPED,
            total_amount=Decimal("200.00"),
            currency="USD",
            fx_rate=Decimal("7.0000"),
            created_at=datetime.now(UTC) - timedelta(days=10),
        ),
        Ticket(message=f"未结-{tag}", created_by="report-test"),
        Ticket(
            message=f"已结-{tag}",
            created_by="report-test",
            status=TicketStatus.CLOSED,
            resolved_at=datetime.now(UTC),
        ),
    )

    after = await _summary()
    assert after["orders"]["total"] - before["orders"]["total"] == 3
    assert _status_count(after, "pending") - _status_count(before, "pending") == 2

    assert after["revenue"]["windowDays"] == 7
    assert after["revenue"]["baseCurrency"] == "CNY"
    assert Decimal(after["revenue"]["amount"]) - Decimal(before["revenue"]["amount"]) == Decimal("712.34")
    assert after["revenue"]["unconverted"] - before["revenue"]["unconverted"] == 1
    assert after["revenue"]["amount"].count(".") == 1  # 2 位小数字符串

    low_ids = {item["productId"] for item in after["lowStock"]}
    assert low.id in low_ids
    assert equal.id not in low_ids  # stock == 阈值不告警(与库存告警同谓词)
    low_item = next(item for item in after["lowStock"] if item["productId"] == low.id)
    assert low_item["title"] == f"低库存-{tag}"
    assert low_item["stock"] == 2 and low_item["alertThreshold"] == 10

    assert after["tickets"]["open"] - before["tickets"]["open"] == 1  # 已结工单不计
