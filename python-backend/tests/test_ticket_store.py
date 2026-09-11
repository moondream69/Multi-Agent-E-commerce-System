"""工单存储缝(issue #21):TicketStore 内存替身语义 + 工单端点流程(离线,不触 PG)。

替身复现生产可见语义:创建倒序、买家名经注入的买家存储解析(无买家为 null)、
结单 open→closed 记 resolved_at 且重复结单幂等;端点路由经 create_app 注入替身(探针工单可见即反证)。
真 PG 的 join 与跨表语义见 test_tickets.py(integration)。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from python_backend.api.app import create_app
from python_backend.core.auth import create_token
from tests.conftest import InMemoryCustomerStore, InMemoryTicketStore


def _client(ticket_store: InMemoryTicketStore, user_id: int = 42) -> TestClient:
    """认证态客户端:JWT 直签(工单端点平权,任意登录者可用;不触登录端点/不触 PG)。"""
    client = TestClient(create_app(auth_required=False, ticket_store=ticket_store))
    client.headers.update({"Authorization": f"Bearer {create_token('tester', user_id)}"})
    return client


# —— 内存替身可见语义(与生产同口径) ——


async def test_list_newest_first_with_customer_name_join() -> None:
    """列表创建倒序;有买家取名字,无买家为 null;信封六键与契约一一对应。"""
    customers = InMemoryCustomerStore()
    buyer = customers.add("工单买家", "ticket-buyer@example.com")
    store = InMemoryTicketStore(customers)
    store.add("先建的工单")
    store.add("后建的工单", customer_id=buyer.id)

    tickets = await store.list_tickets()

    assert [ticket["message"] for ticket in tickets] == ["后建的工单", "先建的工单"]
    assert tickets[0]["customerName"] == "工单买家"
    assert tickets[0]["status"] == "open" and tickets[0]["resolvedAt"] is None
    assert tickets[1]["customerName"] is None
    assert set(tickets[0]) == {"ticketId", "message", "status", "customerName", "createdAt", "resolvedAt"}


async def test_close_records_resolved_at_idempotent() -> None:
    """结单:open→closed 记 resolved_at;重复结单幂等(时间不变);不存在 None(端点 404)。"""
    store = InMemoryTicketStore(InMemoryCustomerStore())
    ticket = store.add("待结单工单")

    closed = await store.close_ticket(ticket.id)
    assert closed is not None and closed["status"] == "closed" and closed["resolvedAt"]

    again = await store.close_ticket(ticket.id)
    assert again is not None and again["resolvedAt"] == closed["resolvedAt"], "重复结单不改时间"

    assert await store.close_ticket(999999) is None


async def test_list_isolates_unknown_customer_id_but_keeps_row() -> None:
    """customer_id 指向不存在的买家:行保留、customerName 为 null(生产 left join 语义,不丢行)。"""
    store = InMemoryTicketStore(InMemoryCustomerStore())
    store.add("孤儿工单", customer_id=999)
    store.add("无买家工单")

    tickets = await store.list_tickets()

    assert [ticket["message"] for ticket in tickets] == ["无买家工单", "孤儿工单"]
    assert all(ticket["customerName"] is None for ticket in tickets)


# —— 端点流程(认证态,注入替身) ——


async def test_ticket_endpoints_use_injected_store() -> None:
    """列表读替身行集(探针工单可见即反证);结单落替身;非法状态 422;不存在 404。"""
    store = InMemoryTicketStore(InMemoryCustomerStore())
    ticket = store.add("端点探针工单")
    client = _client(store)

    listing = client.get("/api/tickets")
    assert listing.status_code == 200
    assert [item["message"] for item in listing.json()["tickets"]] == ["端点探针工单"]

    closed = client.patch(f"/api/tickets/{ticket.id}", json={"status": "closed"})
    assert closed.status_code == 200
    assert closed.json()["ticket"]["status"] == "closed"
    assert (await store.list_tickets())[0]["status"] == "closed", "落在注入替身上"

    assert client.patch(f"/api/tickets/{ticket.id}", json={"status": "reopened"}).status_code == 422
    assert client.patch("/api/tickets/999999", json={"status": "closed"}).status_code == 404
