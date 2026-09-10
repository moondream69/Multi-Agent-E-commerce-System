"""枚举列落库回归(issue #12):多行批插不渲染原生枚举 + 口径 = 小写 value。

- 迁移 0001 五处枚举均声明 native_enum=False(库内为 VARCHAR);模型此前仅由 ``Mapped[X]``
  注解推断类型 —— 推断出原生枚举(类型名 = 类名小写),insertmanyvalues 批插会渲染
  ``::productstatus`` cast 而报 UndefinedObject;口径也随之按 name(大写)落库,
  与迁移声明 / server_default / JSON 契约相左。
- 断言用裸 SQL(不经类型处理器)读回原始字符串,钉死小写口径,防回退。
依赖真 PG;离线秒 skip。
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import Enum, text

from python_backend.db.models import (
    ApprovalBatch,
    ApprovalStatus,
    Order,
    OrderStatus,
    Product,
    Task,
    TaskStatus,
    Ticket,
    TicketStatus,
    User,
)
from python_backend.db.session import SessionFactory
from python_backend.settings import get_settings
from tests.conftest import postgres_reachable

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _require_postgres() -> None:
    if not postgres_reachable(get_settings().database_url):
        pytest.skip("Postgres 离线(compose dev 库),integration 跳过")


async def test_enum_columns_batch_insert_stores_lowercase_values() -> None:
    """五个枚举表:同一 session 内多行 add_all + flush 成功,落库为小写 value,ORM 读回为成员。"""
    # 声明面:与迁移 0001 对齐(非原生枚举,故批插不 cast)
    for model in (Product, Order, Task, ApprovalBatch, Ticket):
        col_type = model.__table__.c.status.type
        assert isinstance(col_type, Enum)
        assert col_type.native_enum is False

    tag = uuid.uuid4().hex[:8]
    user = User(username=f"enum-{tag}", password_hash="x")
    first = Product(sku=f"ENUM-A-{tag}", title=f"枚举A-{tag}", price=Decimal("1.00"), category="测试")
    second = Product(sku=f"ENUM-B-{tag}", title=f"枚举B-{tag}", price=Decimal("1.00"), category="测试")

    async with SessionFactory() as session, session.begin():
        # 修复前:此处 insertmanyvalues 渲染 ::productstatus → ProgrammingError
        session.add_all([user, first, second])
        await session.flush()  # 取父行 id 供外键引用

        order_a = Order(product_id=first.id, total_amount=Decimal("1.00"))
        order_b = Order(product_id=second.id, total_amount=Decimal("2.00"), status=OrderStatus.SHIPPED)
        task_a = Task(thread_id=f"enum-a-{tag}", user_id=user.id, session_id="default", type="order")
        task_b = Task(
            thread_id=f"enum-b-{tag}", user_id=user.id, session_id="default", type="order", status=TaskStatus.FAILED
        )
        batch_a = ApprovalBatch(
            batch_id=f"enum-a-{tag}",
            thread_id=f"enum-a-{tag}",
            slice_no=1,
            action_type="update_price",
            actions=[],
            requested_by="enum-test",
        )
        batch_b = ApprovalBatch(
            batch_id=f"enum-b-{tag}",
            thread_id=f"enum-b-{tag}",
            slice_no=1,
            action_type="update_price",
            actions=[],
            requested_by="enum-test",
            status=ApprovalStatus.EXECUTED,
        )
        ticket_a = Ticket(message=f"枚举工单-{tag}", created_by="enum-test")
        ticket_b = Ticket(message=f"枚举工单已结-{tag}", created_by="enum-test", status=TicketStatus.CLOSED)
        session.add_all([order_a, order_b, task_a, task_b, batch_a, batch_b, ticket_a, ticket_b])

    # 裸 SQL 读回原始字符串(不经类型处理器):口径 = 小写 value
    expected = {
        "products": {first.id: "draft", second.id: "draft"},
        "orders": {order_a.id: "pending", order_b.id: "shipped"},
        "tasks": {task_a.id: "pending", task_b.id: "failed"},
        "approval_batches": {batch_a.id: "pending", batch_b.id: "executed"},
        "tickets": {ticket_a.id: "open", ticket_b.id: "closed"},
    }
    async with SessionFactory() as session:
        for table, rows in expected.items():
            for row_id, want in rows.items():
                raw = (
                    await session.execute(text(f"select status from {table} where id = :id"), {"id": row_id})
                ).scalar_one()
                assert raw == want, f"{table}#{row_id} 落库应为小写 value,实为 {raw!r}"

    # ORM 读回:小写存量经类型处理器还原为枚举成员
    async with SessionFactory() as session:
        order_row = await session.get(Order, order_b.id)
        ticket_row = await session.get(Ticket, ticket_b.id)
        assert order_row is not None
        assert ticket_row is not None
        assert order_row.status is OrderStatus.SHIPPED
        assert ticket_row.status is TicketStatus.CLOSED
