"""conversations 持久化测试(需 docker Postgres,模式同 test_reply_templates)。"""

from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from python_backend.db.base import Base
from python_backend.db.conversation_repo import append_message
from python_backend.db.models import Conversation
from python_backend.db.session import engine

pytestmark = pytest.mark.integration





@pytest.fixture()
def prepared_db():
    Base.metadata.create_all(engine)
    yield


@pytest.fixture()
def clean_rows(prepared_db):
    customer_ids: list[str] = []
    yield customer_ids
    with Session(engine) as session:
        if customer_ids:
            table = cast(Table, Conversation.__table__)
            session.execute(table.delete().where(table.c.customerId.in_(customer_ids)))
            session.commit()


def _fetch(customer_id: str) -> Conversation | None:
    with Session(engine) as session:
        return session.scalar(select(Conversation).where(Conversation.customerId == customer_id))


def test_append_creates_row_with_first_message(prepared_db, clean_rows):
    clean_rows.append("c-1")
    append_message("c-1", "user", "你好")
    row = _fetch("c-1")
    assert row is not None
    assert row.messages[0]["role"] == "user"
    assert row.messages[0]["content"] == "你好"
    assert "timestamp" in row.messages[0]


def test_append_accumulates_messages(prepared_db, clean_rows):
    clean_rows.append("c-2")
    append_message("c-2", "user", "q1")
    append_message("c-2", "assistant", "a1", agent_id="customer-service", task_id="t-1")
    row = _fetch("c-2")
    assert row is not None
    assert len(row.messages) == 2
    assert row.messages[1]["role"] == "assistant"
    assert row.messages[1]["taskId"] == "t-1"
    assert row.agentId == "customer-service"


def test_append_truncates_long_content(prepared_db, clean_rows):
    clean_rows.append("c-3")
    append_message("c-3", "user", "x" * 5000)
    row = _fetch("c-3")
    assert row is not None
    assert len(row.messages[0]["content"]) == 2000
