"""工单存储(spec #11 A11 收口):升级工单只读列表 + 结单。

工单由客服 escalate_ticket 落表(agents/executor);本模块提供界面可见的读取面与人工结单。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from python_backend.db.models import Customer, Ticket, TicketStatus
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


async def list_tickets() -> list[dict]:
    """工单列表(created_at 倒序):升级不再是断头事件(A11)。"""
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(Ticket, Customer.name)
                .outerjoin(Customer, Ticket.customer_id == Customer.id)
                .order_by(Ticket.created_at.desc())
            )
        ).all()
    return [_ticket_payload(ticket, customer_name) for ticket, customer_name in rows]


async def close_ticket(ticket_id: int) -> dict | None:
    """结单:open→closed 记 resolved_at;已结幂等返回(重复请求不报错、不改时间);不存在 None(端点 404)。"""
    async with SessionFactory() as session, session.begin():
        ticket = (await session.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one_or_none()
        if ticket is None:
            return None
        if ticket.status is TicketStatus.OPEN:
            ticket.status = TicketStatus.CLOSED
            ticket.resolved_at = datetime.now(UTC)
        customer_name = None
        if ticket.customer_id is not None:
            customer_name = (
                await session.execute(select(Customer.name).where(Customer.id == ticket.customer_id))
            ).scalar_one_or_none()
        return _ticket_payload(ticket, customer_name)
