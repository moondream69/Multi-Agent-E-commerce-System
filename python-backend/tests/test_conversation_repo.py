"""conversations 持久化测试(需 docker Postgres,模式同 test_reply_templates)。"""

from __future__ import annotations

import time
from typing import cast

import pytest
from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from python_backend.db.base import Base
from python_backend.db.conversation_repo import (
    append_message,
    delete_session,
    list_sessions,
)
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


# —— 多会话(issue #5):sessionId 定位、默认会话回落、标题生成 ——


def _fetch_session(customer_id: str, session_id: str) -> Conversation | None:
    with Session(engine) as session:
        return session.scalar(
            select(Conversation).where(
                Conversation.customerId == customer_id,
                Conversation.sessionId == session_id,
            )
        )


def test_append_without_session_id_falls_back_to_default(prepared_db, clean_rows):
    clean_rows.append("c-4")
    append_message("c-4", "user", "你好")
    row = _fetch_session("c-4", "default")
    assert row is not None
    assert row.messages[0]["content"] == "你好"


def test_append_separates_sessions(prepared_db, clean_rows):
    clean_rows.append("c-5")
    append_message("c-5", "user", "会话A的消息", session_id="s-a")
    append_message("c-5", "user", "会话B的消息", session_id="s-b")
    a = _fetch_session("c-5", "s-a")
    b = _fetch_session("c-5", "s-b")
    assert a is not None and a.messages[0]["content"] == "会话A的消息"
    assert b is not None and b.messages[0]["content"] == "会话B的消息"
    assert len(a.messages) == 1 and len(b.messages) == 1


def test_first_user_message_sets_title_truncated_to_20_chars(prepared_db, clean_rows):
    clean_rows.append("c-6")
    long_text = "这是首条消息" * 5  # 30 字
    append_message("c-6", "user", long_text, session_id="s-c")
    row = _fetch_session("c-6", "s-c")
    assert row is not None
    assert row.title == "这是首条消息" * 3 + "这是"  # 前 20 字


# —— 会话列表 / 删除 ——


def test_list_sessions_returns_meta_newest_first(prepared_db, clean_rows):
    clean_rows.append("c-7")
    append_message("c-7", "user", "第一会话消息", session_id="s-1")
    time.sleep(0.02)
    append_message("c-7", "user", "第二会话消息", session_id="s-2")
    append_message("c-7", "assistant", "回复", session_id="s-2")
    rows = list_sessions("c-7")
    assert [r["sessionId"] for r in rows] == ["s-2", "s-1"]
    assert rows[0]["title"] == "第二会话消息"
    assert rows[0]["messageCount"] == 2


def test_delete_session_removes_only_that_row(prepared_db, clean_rows):
    clean_rows.append("c-8")
    append_message("c-8", "user", "hi", session_id="s-d")
    append_message("c-8", "user", "hi2", session_id="s-keep")
    assert delete_session("c-8", "s-d") is True
    assert _fetch_session("c-8", "s-d") is None
    assert _fetch_session("c-8", "s-keep") is not None
    assert delete_session("c-8", "s-d") is False
