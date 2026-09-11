"""工单存储(spec #11 A11 收口):升级工单只读列表 + 结单。

工单由客服 escalate_ticket 落表(agents/executor);本模块提供界面可见的读取面与人工结单。

TicketStore 协议:端点经 create_app 注入(生产 PostgresTicketStore,测试内存替身)
——离线快速套件不触库(issue #21,与 task_store 同形)。买家名经注入的 CustomerStore 解析,
join 降级为读端点拼装(内容零变化,离线替身也因此可保真)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select

from python_backend.db.customer_store import CustomerStore
from python_backend.db.models import Ticket, TicketStatus
from python_backend.db.session import SessionFactory


def _ticket_payload(ticket: Ticket, customer_name: str | None) -> dict:
    """工单序列化(驼峰,与契约 events.ts TicketItem 字段一一对应)。"""
    return {
        "ticketId": ticket.id,
        "message": ticket.message,
        "status": ticket.status.value,
        "customerName": customer_name,
        "createdAt": ticket.created_at.isoformat() if ticket.created_at else None,
        "resolvedAt": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
    }


class TicketStore(Protocol):
    """工单存储协议(issue #21):列表(含买家名)+ 结单(幂等)。"""

    async def list_tickets(self) -> list[dict]:
        """工单列表(created_at 倒序):升级不再是断头事件(A11)。"""
        ...

    async def close_ticket(self, ticket_id: int) -> dict | None:
        """结单:open→closed 记 resolved_at;已结幂等返回;不存在 None(端点 404)。"""
        ...


class PostgresTicketStore(TicketStore):
    """PG 实现(tickets 表 + 注入的买家存储):issue #21 由模块函数抽为可注入实现。

    列表原本靠 ``outerjoin(Customer)`` 单查;抽缝后改为「工单行 + 逐行取买家名」——
    响应内容与旧 join 等价(无买家为 null),仅是 join 降级为读端点拼装(spec #21 可选延伸)。
    """

    def __init__(self, customer_store: CustomerStore) -> None:
        self._customers = customer_store

    async def list_tickets(self) -> list[dict]:
        async with SessionFactory() as session:
            rows = (await session.execute(select(Ticket).order_by(Ticket.created_at.desc()))).scalars().all()
        return [await self._payload(ticket) for ticket in rows]

    async def close_ticket(self, ticket_id: int) -> dict | None:
        async with SessionFactory() as session, session.begin():
            ticket = (await session.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one_or_none()
            if ticket is None:
                return None
            if ticket.status is TicketStatus.OPEN:
                ticket.status = TicketStatus.CLOSED
                ticket.resolved_at = datetime.now(UTC)
        return await self._payload(ticket)  # 事务外取买家名(订单行已提交,另起读会话)

    async def _payload(self, ticket: Ticket) -> dict:
        name = None if ticket.customer_id is None else await self._customers.find_name(ticket.customer_id)
        return _ticket_payload(ticket, name)
