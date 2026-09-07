"""conversations 会话接口测试(integration,需 docker Postgres,模式同 test_reply_templates)。

issue #5:GET /api/conversations 语义改为会话元数据列表;新增 messages 与 DELETE 端点。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from python_backend.api.app import create_app
from python_backend.api.auth import create_access_token
from python_backend.core.base_agent import BaseAgent
from python_backend.core.event_bus import EventBus
from python_backend.core.orchestrator import Orchestrator
from python_backend.db.conversation_repo import append_message
from python_backend.db.models import Conversation
from python_backend.db.session import SessionLocal
from python_backend.domain.tasks import AgentTask, TaskType
from python_backend.infrastructure.llm import LlmService

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"Authorization": f"Bearer {create_access_token('tester')}"}

USER = "tester"  # 与 AUTH_HEADERS 的 token 主体一致(API 按当前登录用户名查历史)


class StubAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(EventBus(), LlmService())
        self.id = "a1"
        self.name = "测试Agent"
        self.description = "desc"
        self.system_prompt = "test"

    async def execute_task(self, task: AgentTask) -> dict:
        return {"result": "ok"}


def _client() -> TestClient:
    orchestrator = Orchestrator(EventBus())
    orchestrator.register_agent(StubAgent(), TaskType.PRODUCT_RESEARCH)
    return TestClient(create_app(orchestrator), headers=AUTH_HEADERS)


@pytest.fixture()
def clean_conversation():
    yield
    with SessionLocal() as session:
        session.execute(delete(Conversation).where(Conversation.customerId == USER))
        session.commit()


def test_list_conversations_empty(clean_conversation):
    assert _client().get("/api/conversations").json() == []


def test_list_conversations_returns_session_meta(clean_conversation):
    append_message(USER, "user", "第一会话", session_id="s-1")
    append_message(USER, "assistant", "回复", session_id="s-1")
    data = _client().get("/api/conversations").json()
    assert len(data) == 1
    row = data[0]
    assert set(row.keys()) == {"sessionId", "title", "updatedAt", "messageCount"}
    assert row["sessionId"] == "s-1"
    assert row["title"] == "第一会话"
    assert row["messageCount"] == 2


def test_list_conversations_includes_default_session(clean_conversation):
    append_message(USER, "user", "无会话id的消息")  # 缺省回落 default
    data = _client().get("/api/conversations").json()
    assert len(data) == 1
    assert data[0]["sessionId"] == "default"
    assert data[0]["title"] == "无会话id的消息"


def test_get_session_messages(clean_conversation):
    append_message(USER, "user", "你好", task_id="t-1", session_id="s-1")
    append_message(
        USER,
        "assistant",
        "你好,请问有什么可以帮您?",
        agent_id="customer-service",
        task_id="t-1",
        session_id="s-1",
    )
    data = _client().get("/api/conversations/s-1/messages").json()
    assert data["sessionId"] == "s-1"
    messages = data["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "你好"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["taskId"] == "t-1"


def test_get_session_messages_not_found(clean_conversation):
    assert _client().get("/api/conversations/nope/messages").status_code == 404


def test_delete_session(clean_conversation):
    append_message(USER, "user", "要删的", session_id="s-d")
    append_message(USER, "user", "保留的", session_id="s-keep")
    assert _client().delete("/api/conversations/s-d").status_code == 200
    assert _client().get("/api/conversations/s-d/messages").status_code == 404
    remaining = _client().get("/api/conversations").json()
    assert [r["sessionId"] for r in remaining] == ["s-keep"]


def test_delete_session_not_found(clean_conversation):
    assert _client().delete("/api/conversations/nope").status_code == 404
