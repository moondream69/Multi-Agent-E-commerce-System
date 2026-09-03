"""编排器(1:1 迁移 orchestrator.service.spec.ts)。"""

import pytest

from python_backend.core.event_bus import EventBus
from python_backend.core.orchestrator import Orchestrator
from python_backend.domain.agents import AgentStatus
from python_backend.domain.events import AgentEvent
from python_backend.domain.tasks import (
    AgentResult,
    AgentTask,
    TaskStatus,
    TaskType,
    ToolDefinition,
)


class MockAgent:
    id: str
    name: str
    description = "Mock"

    def __init__(self, agent_id: str, name: str) -> None:
        self.id = agent_id
        self.name = name

    async def handle_task(self, task: AgentTask) -> AgentResult:
        return AgentResult(
            task_id=task.id,
            agent_id=self.id,
            status=TaskStatus.COMPLETED,
            output={"result": "ok"},
            steps=[],
            completed_at=task.created_at,
        )

    async def handle_event(self, event: AgentEvent) -> None:
        return None

    def get_status(self) -> AgentStatus:
        return AgentStatus.IDLE

    def get_tools(self) -> list[ToolDefinition]:
        return []


def _task(task_id: str, task_type: TaskType, *, target_agent_id: str | None = None) -> AgentTask:
    return AgentTask(id=task_id, type=task_type, input={}, target_agent_id=target_agent_id)


@pytest.fixture()
def orchestrator() -> Orchestrator:
    return Orchestrator(event_bus=EventBus())


def test_register_agent(orchestrator):
    orchestrator.register_agent(MockAgent("a1", "选品Agent"))
    assert len(orchestrator.get_registered_agents()) == 1


async def test_routes_to_target_agent(orchestrator):
    orchestrator.register_agent(MockAgent("a1", "选品Agent"))
    result = await orchestrator.route_task(_task("t1", TaskType.PRODUCT_RESEARCH, target_agent_id="a1"))
    assert result.status == TaskStatus.COMPLETED


async def test_missing_agent_raises(orchestrator):
    with pytest.raises(ValueError):
        await orchestrator.route_task(_task("t2", TaskType.PRODUCT_RESEARCH, target_agent_id="nonexistent"))


async def test_auto_routes_by_task_type(orchestrator):
    orchestrator.register_agent(MockAgent("r1", "选品Agent"), TaskType.PRODUCT_RESEARCH)
    result = await orchestrator.route_task(_task("t3", TaskType.PRODUCT_RESEARCH))
    assert result.agent_id == "r1"
