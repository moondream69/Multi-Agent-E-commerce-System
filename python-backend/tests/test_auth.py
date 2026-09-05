"""认证测试:令牌层单元(无依赖) + 登录/me 集成(依赖 docker Postgres,模式同 test_reply_templates)。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from python_backend.api.app import create_app
from python_backend.api.auth import create_access_token, hash_password, verify_token
from python_backend.core.base_agent import BaseAgent
from python_backend.core.event_bus import EventBus
from python_backend.core.orchestrator import Orchestrator
from python_backend.db.models import User
from python_backend.db.session import SessionLocal
from python_backend.domain.tasks import AgentTask, TaskType
from python_backend.infrastructure.llm import LlmService

BUS = EventBus()
LLM = LlmService()


class AuthStubAgent(BaseAgent):
    def __init__(self, agent_id: str, name: str) -> None:
        super().__init__(BUS, LLM)
        self.id = agent_id
        self.name = name
        self.description = f"{name}描述"
        self.system_prompt = "test"

    async def execute_task(self, task: AgentTask) -> dict:
        return {"result": "ok"}


def _build_app() -> TestClient:
    orchestrator = Orchestrator(BUS)
    orchestrator.register_agent(AuthStubAgent("a1", "选品"), TaskType.PRODUCT_RESEARCH)
    return TestClient(create_app(orchestrator))


def test_token_roundtrip():
    token = create_access_token("alice")
    assert verify_token(token) == "alice"
    assert verify_token("not-a-jwt") is None
    assert verify_token("") is None


def test_business_routes_reject_anonymous():
    client = _build_app()
    assert client.get("/api/dashboard/agents").status_code == 401
    assert client.get("/api/agents/a1").status_code == 401
    assert client.post("/api/agents/task", json={"type": "product_research", "input": {}}).status_code == 401


def test_invalid_token_rejected():
    client = _build_app()
    headers = {"Authorization": "Bearer invalid.token.here"}
    assert client.get("/api/dashboard/agents", headers=headers).status_code == 401


def test_health_and_me_are_public_or_guarded():
    client = _build_app()
    assert client.get("/health").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_valid_token_accesses_business_routes():
    client = _build_app()
    headers = {"Authorization": f"Bearer {create_access_token('alice')}"}
    assert client.get("/api/dashboard/agents", headers=headers).status_code == 200
    assert (
        client.post(
            "/api/agents/task",
            json={"type": "product_research", "input": {}},
            headers=headers,
        ).status_code
        == 200
    )


@pytest.mark.integration
def test_login_and_me_roundtrip():
    from python_backend.db.base import Base
    from python_backend.db.session import engine

    Base.metadata.create_all(engine)  # 幂等建表,保证 users 存在(与 test_store_api 同模式)
    username = "auth-test-user"
    with SessionLocal() as session:
        if not session.scalar(select(User.id).where(User.username == username)):
            session.add(User(username=username, passwordHash=hash_password("secret-123")))
            session.commit()
    try:
        client = _build_app()
        bad = client.post("/api/auth/login", json={"username": username, "password": "wrong"})
        assert bad.status_code == 401
        ok = client.post("/api/auth/login", json={"username": username, "password": "secret-123"})
        assert ok.status_code == 200
        token = ok.json()["token"]
        assert ok.json()["username"] == username
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["username"] == username
    finally:
        with SessionLocal() as session:
            session.execute(delete(User).where(User.username == username))
            session.commit()
