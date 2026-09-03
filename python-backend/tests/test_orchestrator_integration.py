"""编排器集成测试(1:1 迁移 orchestrator.integration.spec.ts + cross-agent-collaboration.spec.ts)。"""

from __future__ import annotations

import asyncio

import pytest

from python_backend.core.base_agent import BaseAgent
from python_backend.core.event_bus import EventBus
from python_backend.core.orchestrator import Orchestrator
from python_backend.domain.agents import AgentStatus
from python_backend.domain.events import AgentEvent, AgentEventType
from python_backend.domain.tasks import AgentResult, AgentTask, TaskStatus, TaskType, ToolDefinition
from python_backend.infrastructure.llm import LlmService

LLM = LlmService()


class ResearchStubAgent(BaseAgent):
    id = "research-1"
    name = "选品Agent"
    description = "选品"
    system_prompt = "test"

    async def execute_task(self, task: AgentTask) -> dict:
        return {"report": "分析结果"}


class OrderStubAgent(BaseAgent):
    id = "order-1"
    name = "订单Agent"
    description = "订单"
    system_prompt = "test"

    def __init__(self) -> None:
        super().__init__(EventBus(), LLM)
        self.received_events: list[AgentEvent] = []

    async def execute_task(self, task: AgentTask) -> dict:
        if task.input.get("action") == "create_product":
            self.received_events.append(
                AgentEvent(
                    id="ev-1",
                    type=AgentEventType.REPORT_GENERATED,
                    source="research-1",
                    timestamp=task.created_at,
                    payload={},
                )
            )
        return {"product": {"id": "prod-1"}}

    async def handle_event(self, event: AgentEvent) -> None:
        self.received_events.append(event)


class ServiceStubAgent(BaseAgent):
    id = "service-1"
    name = "客服Agent"
    description = "客服"
    system_prompt = "test"

    async def execute_task(self, task: AgentTask) -> dict:
        return {"reply": "感谢您的咨询"}


@pytest.fixture()
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture()
def orchestrator(event_bus) -> Orchestrator:
    return Orchestrator(event_bus)


async def test_full_flow_register_route_and_status(orchestrator, event_bus):
    status_events: list[dict] = []
    event_bus.on(AgentEventType.AGENT_STATUS_CHANGED, lambda e: status_events.append(e.payload))
    agent = ResearchStubAgent(event_bus, LLM)
    orchestrator.register_agent(agent, TaskType.PRODUCT_RESEARCH)

    result = await orchestrator.route_task(
        AgentTask(id="task-int-1", type=TaskType.PRODUCT_RESEARCH, input={"query": "蓝牙耳机市场趋势"})
    )
    assert result.status == TaskStatus.COMPLETED
    assert "report" in result.output
    assert any(p["status"] == AgentStatus.BUSY.value and p["agentId"] == "research-1" for p in status_events)
    assert any(p["status"] == AgentStatus.IDLE.value for p in status_events)


async def test_broadcast_event_notifies_all_agents(orchestrator, event_bus):
    order_agent = OrderStubAgent()
    orchest = Orchestrator(event_bus)
    orchest.register_agent(order_agent)

    await orchest.broadcast_event(AgentEventType.REPORT_GENERATED, {"reportId": "r-1"})
    assert any(e.type == AgentEventType.REPORT_GENERATED for e in order_agent.received_events)


async def test_concurrent_tasks_stay_busy_until_last_finishes(orchestrator, event_bus):
    status_events: list[dict] = []
    event_bus.on(AgentEventType.AGENT_STATUS_CHANGED, lambda e: status_events.append(e.payload))

    class SlowAgent(ResearchStubAgent):
        def __init__(self, gate: asyncio.Event) -> None:
            super().__init__(event_bus, LLM)
            self._gate = gate

        async def execute_task(self, task: AgentTask) -> dict:
            if task.id == "c1":
                await self._gate.wait()
            return {"result": task.id}

    gate = asyncio.Event()
    agent = SlowAgent(gate)
    orchestrator.register_agent(agent, TaskType.PRODUCT_RESEARCH)

    first = asyncio.create_task(
        orchestrator.route_task(AgentTask(id="c1", type=TaskType.PRODUCT_RESEARCH, input={"query": "first"}))
    )
    await asyncio.sleep(0.02)
    second = asyncio.create_task(
        orchestrator.route_task(AgentTask(id="c2", type=TaskType.PRODUCT_RESEARCH, input={"query": "second"}))
    )

    await second
    assert agent.get_status() == AgentStatus.BUSY

    gate.set()
    await first
    assert agent.get_status() == AgentStatus.IDLE

    assert (status_events[0]["status"], status_events[0]["taskId"]) == (AgentStatus.BUSY.value, "c1")
    assert (status_events[-1]["status"], status_events[-1]["taskId"]) == (AgentStatus.IDLE.value, "c1")


async def test_cross_agent_collaboration(orchestrator):
    research = ResearchStubAgent(EventBus(), LLM)
    order = OrderStubAgent()
    service = ServiceStubAgent(EventBus(), LLM)
    orchestrator.register_agent(research, TaskType.PRODUCT_RESEARCH)
    orchestrator.register_agent(order, TaskType.ORDER_MANAGEMENT)
    orchestrator.register_agent(service, TaskType.CUSTOMER_SERVICE)

    report = await orchestrator.route_task(AgentTask(id="collab-1", type=TaskType.PRODUCT_RESEARCH, input={"query": "蓝牙耳机"}))
    assert report.status == TaskStatus.COMPLETED

    order_result = await orchestrator.route_task(
        AgentTask(id="collab-2", type=TaskType.ORDER_MANAGEMENT, input={"action": "create_product", "sku": "BT-001", "title": "蓝牙耳机", "price": 99, "category": "电子"})
    )
    assert order_result.status == TaskStatus.COMPLETED

    service_result = await orchestrator.route_task(AgentTask(id="collab-3", type=TaskType.CUSTOMER_SERVICE, input={"action": "handle_query", "text": "你好"}))
    assert service_result.status == TaskStatus.COMPLETED
