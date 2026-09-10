"""买家存储(spec #11):只读列表 + 演示买家 seed。

买家数据入口 = CSV 导入(core/imports)+ 本模块的 dev seed;订单导入与模拟流量买家池以此为前提。
seed 仅 dev(生产不落模拟数据),按 email 幂等——与管理员懒 seed(core/auth)同模式。
"""

from __future__ import annotations

from sqlalchemy import select

from python_backend.db.models import Customer
from python_backend.db.session import SessionFactory
from python_backend.settings import get_settings

# 演示买家(CONTEXT「演示买家」;spec #11:演练环境首次启动即有买家可用)
DEMO_BUYERS: list[dict] = [
    {"name": "张伟", "email": "zhangwei@example.com", "locale": "zh-CN"},
]


async def list_customers() -> list[dict]:
    """买家列表(created_at 倒序):{customerId, name, email, locale}——模拟流量买家池 + 运营查询。"""
    async with SessionFactory() as session:
        rows = (await session.execute(select(Customer).order_by(Customer.created_at.desc()))).scalars().all()
        return [{"customerId": row.id, "name": row.name, "email": row.email, "locale": row.locale} for row in rows]


async def ensure_demo_buyers() -> None:
    """演示买家懒 seed(启动时调用,幂等;仅 dev):按 email 判重,不覆盖既有行。"""
    if get_settings().environment != "dev":
        return
    async with SessionFactory() as session, session.begin():
        for buyer in DEMO_BUYERS:
            existing = (
                await session.execute(select(Customer.id).where(Customer.email == buyer["email"]))
            ).scalar_one_or_none()
            if existing is None:
                session.add(Customer(name=buyer["name"], email=buyer["email"], locale=buyer["locale"]))
