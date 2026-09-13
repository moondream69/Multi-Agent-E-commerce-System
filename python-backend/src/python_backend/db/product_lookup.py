"""商品定位存储(issue #35):订单 Agent 工具 product_lookup 的数据源。

与 product_store(#34 数据台只读列表)分开:那是端点族的界面数据面,
本模块是 Agent 工具族的 SKU/标题定位——操作前先把口语指代(SKU/标题)解析成 ID。

ProductLookupStore 协议:经 ToolExecutor 注入位注入(生产 PostgresProductLookup,
测试内存替身),工具执行链进离线快速套件(与 #34 端点缝方向一致)。

issue #39 补第二族:ProductMentionSearcher(反向查询——**消息文本里找商品**,
起草工作台查证用;方向与 lookup 相反故独立协议,payload 复用同一序列化器)。
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import func, literal, or_, select

from python_backend.db.models import Product
from python_backend.db.session import SessionFactory

TITLE_MATCH_LIMIT = 10
MENTION_MATCH_LIMIT = 5


def _product_payload(product: Product) -> dict:
    """商品序列化(驼峰;字段沿用 product_store 形状,供 Agent 消歧与识别)。"""
    return {
        "id": product.id,
        "sku": product.sku,
        "title": product.title,
        "price": str(product.price),
        "currency": product.currency,
        "category": product.category,
        "status": product.status.value,
        "stock": product.stock,
    }


class ProductLookupStore(Protocol):
    """商品定位存储协议(issue #35):唯一操作 = 按 SKU/标题查询(纯只读)。"""

    async def lookup(self, *, sku: str | None, title: str | None, limit: int) -> tuple[list[dict], bool]:
        """按 SKU(精确,大小写不敏感)或标题(contains,大小写不敏感)查询。

        返回 (命中列表, 是否截断)。sku 优先;SKU 唯一约束故至多一条。
        两个入参至少给一(均缺显式报错,不猜)。
        """
        ...


class PostgresProductLookup(ProductLookupStore):
    """PG 实现(products 表)。"""

    async def lookup(self, *, sku: str | None, title: str | None, limit: int) -> tuple[list[dict], bool]:
        async with SessionFactory() as session:
            if sku:
                statement = select(Product).where(func.lower(Product.sku) == sku.strip().lower())
            elif title:
                statement = select(Product).where(Product.title.ilike(f"%{title.strip()}%"))
            else:
                raise ValueError("product_lookup 需提供 sku 或 title 至少其一")
            statement = statement.order_by(Product.id)
            # limit+1 探测截断:命中超上限时如实回 truncated,不静默截断
            rows = (await session.execute(statement.limit(limit + 1))).scalars().all()
        truncated = len(rows) > limit
        return [_product_payload(product) for product in rows[:limit]], truncated


class ProductMentionSearcher(Protocol):
    """商品指代检索协议(issue #39):从买家消息文本里找商品(方向与 lookup 相反)。

    起草工作台查证用:消息含商品标题或其首段(核心名词)即命中。返回值含截断标志,
    无命中 → 空列表(证据如实,不编造)。
    """

    async def find_mentions(self, message: str, *, limit: int) -> tuple[list[dict], bool]:
        """返回 (命中列表, 是否截断)。"""
        ...


class PostgresProductMentionSearcher(ProductMentionSearcher):
    """PG 实现:``message ILIKE '%'||title||'%'`` 或含标题首段时命中,limit+1 探测截断。

    标题首段覆盖带色号/款式后缀的商品名(「宠物饮水机 雾灰款」→ 核心「宠物饮水机」)。
    """

    async def find_mentions(self, message: str, *, limit: int) -> tuple[list[dict], bool]:
        pattern = func.concat("%", Product.title, "%")
        core = func.concat("%", func.split_part(Product.title, " ", 1), "%")
        async with SessionFactory() as session:
            rows = (
                (
                    await session.execute(
                        select(Product)
                        .where(or_(literal(message).ilike(pattern), literal(message).ilike(core)))
                        .order_by(Product.id)
                        .limit(limit + 1)
                    )
                )
                .scalars()
                .all()
            )
        truncated = len(rows) > limit
        return [_product_payload(product) for product in rows[:limit]], truncated
