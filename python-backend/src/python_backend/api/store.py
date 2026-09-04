"""买家前台 REST 路由:商品浏览 + 下单 + 订单列表(演示买家直购)。

下单走 REST(页面「立即购买」),订单状态流转仍由订单管理 Agent 负责;
订单创建与状态变更均 emit ORDER_STATUS_CHANGED,前端经 WS 实时刷新。
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from python_backend.api.schemas import CreateOrderDto
from python_backend.core.event_bus import EventBus
from python_backend.db.models import Customer, Order, OrderStatus, Product
from python_backend.db.rows import row_to_dict
from python_backend.db.session import SessionLocal
from python_backend.domain.events import AgentEventType

logger = logging.getLogger(__name__)

# 固定演示买家(seed.py 按 email 幂等保证存在)
DEMO_BUYER_EMAIL = "zhangwei@example.com"


def _order_to_dict(order: Order) -> dict:
    """订单行 → 契约形状:驼峰关联键 + 嵌套商品对象。"""
    data = row_to_dict(order)
    data["productId"] = data.pop("product_id")
    data["customerId"] = data.pop("customer_id")
    data["product"] = row_to_dict(order.product) if order.product else None
    return data


def build_store_router(event_bus: EventBus | None = None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/products")
    async def list_products(category: str | None = None) -> list[dict]:
        with SessionLocal() as session:
            query = session.query(Product).where(Product.status == "active")
            if category:
                query = query.where(Product.category == category)
            return [row_to_dict(p) for p in query.order_by(Product.createdAt.desc()).all()]

    @router.get("/api/products/{product_id}")
    async def get_product(product_id: str) -> dict:
        try:
            key = uuid.UUID(product_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="无效的商品 ID") from error
        with SessionLocal() as session:
            product = session.get(Product, key)
            if product is None:
                raise HTTPException(status_code=404, detail="商品未找到")
            return row_to_dict(product)

    @router.post("/api/orders")
    async def create_order(dto: CreateOrderDto) -> dict:
        try:
            product_id = uuid.UUID(dto.productId)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="无效的商品 ID") from error
        with SessionLocal() as session:
            product = session.get(Product, product_id)
            if product is None:
                raise HTTPException(status_code=404, detail="商品未找到")
            customer = session.scalar(select(Customer).where(Customer.email == DEMO_BUYER_EMAIL))
            order = Order(
                product_id=product.id,
                customer_id=customer.id if customer else None,
                status=OrderStatus.PENDING,
                totalAmount=Decimal(str(dto.totalAmount)) if dto.totalAmount is not None else product.price,
            )
            session.add(order)
            session.commit()
            session.refresh(order)
            data = _order_to_dict(order)
        if event_bus is not None:
            event_bus.emit(
                AgentEventType.ORDER_STATUS_CHANGED,
                {
                    "orderId": data["id"],
                    "from": None,
                    "to": OrderStatus.PENDING.value,
                    "productId": data["productId"],
                    "totalAmount": data["totalAmount"],
                },
                source="store",
            )
        return data

    @router.get("/api/orders")
    async def list_orders() -> list[dict]:
        with SessionLocal() as session:
            orders = session.query(Order).order_by(Order.createdAt.desc()).all()
            return [_order_to_dict(o) for o in orders]

    return router
