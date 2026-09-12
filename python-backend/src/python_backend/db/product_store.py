"""商品只读存储(spec #34):数据台盘货数据源。

商品行由 CSV 导入 / Agent 草稿落表;本模块提供界面可见的只读列表(无任何写操作)。

ProductStore 协议:端点经 create_app 注入(生产 PostgresProductStore,测试内存替身)
——离线快速套件不触库(issue #21 缝口径,与 report_store 同形;本文件补的是 #9
遗留的直连 SessionFactory 端点)。
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import select

from python_backend.db.models import Product
from python_backend.db.session import SessionFactory


def _product_payload(product: Product) -> dict:
    """商品序列化(驼峰,与契约 events.ts ProductListItem 字段一一对应)。"""
    return {
        "id": product.id,
        "sku": product.sku,
        "title": product.title,
        "price": str(product.price),
        "currency": product.currency,
        "category": product.category,
        "status": product.status.value,
        "stock": product.stock,
        "alertThreshold": product.alert_threshold,
    }


class ProductStore(Protocol):
    """商品存储协议(spec #34):唯一操作 = 只读列表。"""

    async def list_products(self) -> list[dict]:
        """商品列表(created_at 倒序):模拟流量发现商品 + 数据台盘货数据源(spec #9)。"""
        ...


class PostgresProductStore(ProductStore):
    """PG 实现(products 表):issue #34 由端点内直查抽为可注入实现,行为零变化。"""

    async def list_products(self) -> list[dict]:
        async with SessionFactory() as session:
            rows = (await session.execute(select(Product).order_by(Product.created_at.desc()))).scalars().all()
        return [_product_payload(product) for product in rows]
