"""报表聚合(spec #11):经营快照 —— 订单分布 / 近 7 日成交额(CNY 快照口径)/ 低库存 / 未结工单。

纯 SQL 聚合零 LLM;金额经 Decimal 量化 2 位(不随浮点漂移);
低库存谓词与库存告警一致(stock < alert_threshold),不另写一份阈值逻辑。

ReportStore 协议:端点经 create_app 注入(生产 PostgresReportStore,测试内存替身)
——离线快速套件不触库(issue #21,与 task_store 同形)。聚合口径的正确性证明仍在集成测试。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from sqlalchemy import case, func, select

from python_backend.db.models import Order, Product, Ticket, TicketStatus
from python_backend.db.session import SessionFactory

REVENUE_WINDOW_DAYS = 7


class ReportStore(Protocol):
    """报表聚合协议(issue #21):唯一操作 = 组装经营快照信封。"""

    async def build_summary(self) -> dict:
        """经营快照:订单状态分布、近 7 日成交额(缺汇率单列待核)、低库存商品、未结工单数。"""
        ...


class PostgresReportStore(ReportStore):
    """PG 实现(纯 SQL 聚合):issue #21 由模块函数抽为可注入实现,行为零变化。"""

    async def build_summary(self) -> dict:
        window_start = datetime.now(UTC) - timedelta(days=REVENUE_WINDOW_DAYS)
        async with SessionFactory() as session:
            status_rows = (await session.execute(select(Order.status, func.count()).group_by(Order.status))).all()
            by_status = {status.value: count for status, count in status_rows}

            revenue_sum, unconverted = (
                await session.execute(
                    select(
                        func.sum(Order.total_amount * Order.fx_rate),
                        func.count(case((Order.fx_rate.is_(None), 1))),
                    ).where(Order.created_at >= window_start)
                )
            ).one()
            amount = Decimal("0") if revenue_sum is None else Decimal(revenue_sum)
            amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            low_stock = (
                (
                    await session.execute(
                        select(Product).where(Product.stock < Product.alert_threshold).order_by(Product.stock.asc())
                    )
                )
                .scalars()
                .all()
            )

            open_tickets = (
                await session.execute(
                    select(func.count()).select_from(Ticket).where(Ticket.status == TicketStatus.OPEN)
                )
            ).scalar_one()

        return {
            "orders": {"byStatus": by_status, "total": sum(by_status.values())},
            "revenue": {
                "windowDays": REVENUE_WINDOW_DAYS,
                "baseCurrency": "CNY",
                "amount": str(amount),
                "unconverted": unconverted or 0,
            },
            "lowStock": [
                {
                    "productId": product.id,
                    "sku": product.sku,
                    "title": product.title,
                    "stock": product.stock,
                    "alertThreshold": product.alert_threshold,
                }
                for product in low_stock
            ],
            "tickets": {"open": open_tickets},
        }
