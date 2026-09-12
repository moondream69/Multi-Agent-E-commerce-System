"""订单只读存储(spec #34):数据台对账列表 + 汇率卡片走势数据源。

订单由 CSV 导入 / REST 下单 / Agent `order.create` apply 落表;本模块只提供读面。

OrderStore 协议:端点经 create_app 注入(生产 PostgresOrderStore,测试内存替身)
——离线快速套件不触库(issue #21 缝口径)。

两操作:
- list_orders:状态筛选 + 分页 + 同筛选总数(created_at 倒序;订单表随导入/流量持续增长,
  前端全量拉取会线性劣化,故边界放服务端 —— spec #34 §A)
- daily_fx_snapshots:近 N 日按日汇率快照(当日最后一笔;无单日不产出点),供汇率卡片走势
  (上游 open.er-api.com 免费端点无时序能力,走势取真实成交口径 = 订单快照)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select

from python_backend.db.models import Order, OrderStatus
from python_backend.db.session import SessionFactory

DEFAULT_ORDER_LIMIT = 50
MAX_ORDER_LIMIT = 200


def _order_payload(order: Order) -> dict:
    """订单序列化(驼峰,与契约 events.ts OrderListItem 字段一一对应)。"""
    return {
        "id": order.id,
        "reference": order.reference,
        "productId": order.product_id,
        "customerId": order.customer_id,
        "status": order.status.value,
        "totalAmount": str(order.total_amount),
        "currency": order.currency,
        "fxRate": str(order.fx_rate) if order.fx_rate is not None else None,
        "fxBaseCurrency": order.fx_base_currency,
        "platform": order.platform,
        "createdAt": order.created_at.isoformat() if order.created_at else None,
    }


class OrderStore(Protocol):
    """订单存储协议(spec #34):只读列表 + 汇率快照时序聚合。"""

    async def list_orders(self, *, status: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        """订单列表 + 同筛选条件下的总行数(created_at 倒序)。"""
        ...

    async def daily_fx_snapshots(self, *, days: int, currency: str) -> list[dict]:
        """近 N 日按日汇率快照 [{date, rate}]:币种筛选,当日最后一笔;无单日不产出点。"""
        ...


class PostgresOrderStore(OrderStore):
    """PG 实现(orders 表):纯只读聚合,无事务面。"""

    async def list_orders(self, *, status: str | None, limit: int, offset: int) -> tuple[list[dict], int]:
        conditions = [] if status is None else [Order.status == OrderStatus(status)]
        async with SessionFactory() as session:
            total = (await session.execute(select(func.count()).select_from(Order).where(*conditions))).scalar_one()
            rows = (
                (
                    await session.execute(
                        select(Order)
                        .where(*conditions)
                        # id 次序作稳定 tie-breaker:同秒落库的多行翻页不重不漏
                        .order_by(Order.created_at.desc(), Order.id.desc())
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
        return [_order_payload(order) for order in rows], total

    async def daily_fx_snapshots(self, *, days: int, currency: str) -> list[dict]:
        window_start = datetime.now(UTC) - timedelta(days=days)
        # DISTINCT ON(PG 专属):按日取当日最后一笔 —— 日界随库会话时区(容器默认 UTC)
        day = func.date_trunc("day", Order.created_at)
        async with SessionFactory() as session:
            rows = (
                await session.execute(
                    select(day.label("day"), Order.fx_rate)
                    .where(
                        Order.created_at >= window_start,
                        Order.currency == currency,
                        # 缺汇率快照的单不产出点:待核由快照条/卡片「待核」态承担,不造中间值
                        Order.fx_rate.is_not(None),
                    )
                    .distinct(day)
                    .order_by(day, Order.created_at.desc())
                )
            ).all()
        return [{"date": row.day.strftime("%Y-%m-%d"), "rate": str(row.fx_rate)} for row in rows]
