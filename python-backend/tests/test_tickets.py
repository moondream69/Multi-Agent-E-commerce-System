"""工单可见列表测试(spec #11 A11):GET /api/tickets + PATCH 结单。

- 列表:created_at 倒序;customerName 由 customer_id join(无买家为 null)
- 结单:open→closed 记 resolved_at;重复结单幂等;body 非法状态 422;不存在 404
- 平权:任何登录者可看可结(与审批模型一致)
依赖真 PG;离线秒 skip。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from python_backend.api.app import create_app
from python_backend.core.auth import create_token, hash_password
from python_backend.db.models import Customer, Ticket, User
from python_backend.db.session import SessionFactory

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("requires_postgres")]


async def _seed_user() -> tuple[int, str]:
    async with SessionFactory() as session, session.begin():
        username = f"ticket-{uuid.uuid4().hex[:8]}"
        user = User(username=username, password_hash=hash_password("pw"))
        session.add(user)
        await session.flush()
        return user.id, username


async def _seed_ticket(*, created_by: str, message: str, customer_id: int | None = None) -> int:
    async with SessionFactory() as session, session.begin():
        ticket = Ticket(message=message, created_by=created_by, customer_id=customer_id)
        session.add(ticket)
        await session.flush()
        return ticket.id


def _client(user_id: int, username: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app(auth_required=True)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {create_token(username, user_id)}"},
    )


async def test_ticket_list_order_and_customer_join() -> None:
    """工单列表:创建倒序;关联买家名 join,无买家为 null;字段形状固定。"""
    user_id, username = await _seed_user()
    tag = uuid.uuid4().hex[:8]
    async with SessionFactory() as session, session.begin():
        customer = Customer(name=f"工单买家-{tag}", email=f"ticket-{tag}@example.com")
        session.add(customer)
        await session.flush()
        customer_id = customer.id
    await _seed_ticket(created_by=username, message=f"先建的工单-{tag}")
    await _seed_ticket(created_by=username, message=f"后建的工单-{tag}", customer_id=customer_id)

    async with _client(user_id, username) as client:
        response = await client.get("/api/tickets")
    assert response.status_code == 200
    tickets = response.json()["tickets"]
    messages = [ticket["message"] for ticket in tickets]
    assert messages.index(f"后建的工单-{tag}") < messages.index(f"先建的工单-{tag}")  # 倒序
    with_customer = tickets[messages.index(f"后建的工单-{tag}")]
    without_customer = tickets[messages.index(f"先建的工单-{tag}")]
    assert with_customer["customerName"] == f"工单买家-{tag}"
    assert with_customer["status"] == "open"
    assert with_customer["resolvedAt"] is None
    assert without_customer["customerName"] is None
    assert set(with_customer) == {"ticketId", "message", "status", "customerName", "createdAt", "resolvedAt"}


async def test_ticket_close_records_resolved_at_idempotent() -> None:
    """结单:open→closed 记 resolved_at;重复结单幂等(时间不变)。"""
    user_id, username = await _seed_user()
    ticket_id = await _seed_ticket(created_by=username, message="待结单工单")
    async with _client(user_id, username) as client:
        response = await client.patch(f"/api/tickets/{ticket_id}", json={"status": "closed"})
        assert response.status_code == 200
        ticket = response.json()["ticket"]
        assert ticket["status"] == "closed" and ticket["resolvedAt"]

        again = await client.patch(f"/api/tickets/{ticket_id}", json={"status": "closed"})
        assert again.status_code == 200
        assert again.json()["ticket"]["resolvedAt"] == ticket["resolvedAt"]


async def test_ticket_close_validation_and_404() -> None:
    """body 非法状态 422(pydantic Literal);不存在 404。"""
    user_id, username = await _seed_user()
    async with _client(user_id, username) as client:
        bad = await client.patch("/api/tickets/1", json={"status": "reopened"})
        assert bad.status_code == 422
        missing = await client.patch("/api/tickets/999999999", json={"status": "closed"})
        assert missing.status_code == 404


async def test_ticket_ops_are_flat_privilege() -> None:
    """平权:非创建者也可列表与结单(与审批「任何登录者可决定」同模型)。"""
    _creator_id, creator = await _seed_user()
    other_id, other = await _seed_user()
    ticket_id = await _seed_ticket(created_by=creator, message="平权结单工单")
    async with _client(other_id, other) as client:
        listing = await client.get("/api/tickets")
        assert any(ticket["ticketId"] == ticket_id for ticket in listing.json()["tickets"])
        closed = await client.patch(f"/api/tickets/{ticket_id}", json={"status": "closed"})
        assert closed.status_code == 200
        assert closed.json()["ticket"]["status"] == "closed"
