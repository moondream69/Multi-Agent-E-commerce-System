"""买家存储(spec #11):只读列表 + 演示买家 seed。

买家数据入口 = CSV 导入(core/imports)+ 本模块的 dev seed;订单导入与模拟流量买家池以此为前提。
seed 仅 dev(生产不落模拟数据),按 email 幂等——与管理员懒 seed(core/auth)同模式。

CustomerStore 协议:端点与启动 seed 经 create_app 注入(生产 PostgresCustomerStore,测试内存替身)
——离线快速套件不触库(issue #21,与 task_store 同形)。
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import select

from python_backend.db.models import Customer
from python_backend.db.session import SessionFactory
from python_backend.settings import get_settings

# 演示买家(CONTEXT「演示买家」;spec #11:演练环境首次启动即有买家可用)
DEMO_BUYERS: list[dict] = [
    {"name": "张伟", "email": "zhangwei@example.com", "locale": "zh-CN"},
]


class CustomerStore(Protocol):
    """买家存储协议(issue #21):列表 + 懒 seed + 按 id 取名字(工单列表 join 展示用)。"""

    async def list_customers(self) -> list[dict]:
        """买家列表(created_at 倒序):{customerId, name, email, locale}。"""
        ...

    async def ensure_demo_buyers(self) -> None:
        """演示买家懒 seed(启动时调用,幂等;仅 dev):按 email 判重,不覆盖既有行。"""
        ...

    async def find_name(self, customer_id: int) -> str | None:
        """按 id 取买家名;不存在 None(工单列表外连接语义:无买家为 null)。"""
        ...


class PostgresCustomerStore(CustomerStore):
    """PG 实现(customers 表):issue #21 由模块函数抽为可注入实现,行为零变化。"""

    async def list_customers(self) -> list[dict]:
        """模拟流量买家池 + 运营查询。"""
        async with SessionFactory() as session:
            rows = (await session.execute(select(Customer).order_by(Customer.created_at.desc()))).scalars().all()
            return [{"customerId": row.id, "name": row.name, "email": row.email, "locale": row.locale} for row in rows]

    async def ensure_demo_buyers(self) -> None:
        # 逐次读 module 级 DEMO_BUYERS / get_settings:测试 monkeypatch 这两处切换探针与环境剖面
        if get_settings().environment != "dev":
            return
        async with SessionFactory() as session, session.begin():
            for buyer in DEMO_BUYERS:
                existing = (
                    await session.execute(select(Customer.id).where(Customer.email == buyer["email"]))
                ).scalar_one_or_none()
                if existing is None:
                    session.add(Customer(name=buyer["name"], email=buyer["email"], locale=buyer["locale"]))

    async def find_name(self, customer_id: int) -> str | None:
        async with SessionFactory() as session:
            return (await session.execute(select(Customer.name).where(Customer.id == customer_id))).scalar_one_or_none()
