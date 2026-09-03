"""契约测试:断言输出形状与前端手抄类型文件(../frontend/src/types/events.ts)一致。

真源是 frontend/src/types/events.ts ——任何字段增减都必须回到该文件保持手同步。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from python_backend.api.app import create_app
from python_backend.core.base_agent import BaseAgent
from python_backend.core.event_bus import EventBus
from python_backend.core.orchestrator import Orchestrator
from python_backend.domain.tasks import AgentTask, TaskType
from python_backend.infrastructure.llm import LlmService

BUS = EventBus()
LLM = LlmService()


class ContractStubAgent(BaseAgent):
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
    orchestrator.register_agent(ContractStubAgent("a1", "选品"), TaskType.PRODUCT_RESEARCH)
    orchestrator.register_agent(ContractStubAgent("a2", "订单"), TaskType.ORDER_MANAGEMENT)
    orchestrator.register_agent(ContractStubAgent("a3", "客服"), TaskType.CUSTOMER_SERVICE)
    return TestClient(create_app(orchestrator))


def test_root_and_health():
    client = _build_app()
    assert client.get("/").json() == {"message": "Hello World!"}
    assert client.get("/health").json() == {"status": "ok"}


def test_dashboard_agents_shape():
    client = _build_app()
    data = client.get("/api/dashboard/agents").json()
    assert len(data) == 3
    for agent in data:
        assert set(agent.keys()) == {"id", "name", "description", "status", "tools"}
        assert agent["status"] in ("idle", "busy", "error", "offline")
        assert isinstance(agent["tools"], list)
        if agent["tools"]:
            tool = agent["tools"][0]
            assert set(tool.keys()) == {"name", "description", "parameters"}
            if tool["parameters"]:
                assert set(tool["parameters"][0].keys()) == {"name", "type", "description", "required"}


def test_dashboard_status_shape():
    client = _build_app()
    data = client.get("/api/dashboard/status").json()
    assert set(data.keys()) == {"totalAgents", "onlineAgents", "timestamp"}
    assert data["totalAgents"] == 3


def test_create_task_result_shape():
    client = _build_app()
    response = client.post("/api/agents/task", json={"type": "product_research", "input": {"query": "test"}})
    assert response.status_code == 200
    result = response.json()
    assert set(result.keys()) == {"taskId", "agentId", "status", "output", "steps", "completedAt"}
    assert result["agentId"] == "a1"
    assert result["steps"][0].keys() == {"name", "status", "detail", "startedAt", "completedAt"}


def test_create_task_invalid_body_rejected():
    client = _build_app()
    assert client.post("/api/agents/task", json={"type": "", "input": {}}).status_code == 422
    assert client.post("/api/agents/task", json={"input": {}}).status_code == 422


def test_get_agent_by_id_and_not_found():
    client = _build_app()
    data = client.get("/api/agents/a1").json()
    assert set(data.keys()) == {"id", "name", "status", "tools"}
    assert client.get("/api/agents/nope").json() == {"error": "Agent not found"}


def test_event_json_shape_matches_events_ts():
    from python_backend.core.event_bus import new_event
    from python_backend.api.serializers import event_payload
    from python_backend.domain.events import AgentEventType

    event = new_event(AgentEventType.REPORT_GENERATED, {"reportId": "r-1"}, correlation_id="corr-1")
    data = event_payload(event)
    assert set(data.keys()) == {"id", "type", "source", "timestamp", "payload", "correlationId"}
    assert data["type"] == "report.generated"
    assert data["correlationId"] == "corr-1"
