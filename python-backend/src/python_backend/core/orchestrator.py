"""编排器:Agent 注册 + 任务路由(镜像 orchestrator.service.ts)。"""

from __future__ import annotations

import asyncio
import logging

from python_backend.core.event_bus import EventBus, new_event
from python_backend.db.agent_task_repo import record_task_end, record_task_start
from python_backend.db.models import AgentTaskStatus
from python_backend.domain.agents import AgentProtocol
from python_backend.domain.events import AgentEventType
from python_backend.domain.tasks import AgentResult, AgentTask, TaskStatus, TaskType

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._agents: dict[str, AgentProtocol] = {}
        self._task_type_routing: dict[TaskType, str] = {}

    def register_agent(self, agent: AgentProtocol, default_task_type: TaskType | None = None) -> None:
        self._agents[agent.id] = agent
        if default_task_type is not None:
            self._task_type_routing[default_task_type] = agent.id
        logger.info("Agent 已注册: %s (%s)", agent.name, agent.id)

    def get_registered_agents(self) -> list[AgentProtocol]:
        return list(self._agents.values())

    def get_agent(self, agent_id: str) -> AgentProtocol | None:
        return self._agents.get(agent_id)

    async def route_task(self, task: AgentTask) -> AgentResult:
        target_agent_id = task.target_agent_id or self._task_type_routing.get(task.type)
        if target_agent_id is None:
            raise ValueError(f"无法路由任务: TaskType {task.type} 未注册路由")
        agent = self._agents.get(target_agent_id)
        if agent is None:
            raise ValueError(f"Agent {target_agent_id} 未注册")

        logger.info("路由任务 %s → %s", task.id, agent.name)
        try:
            record_task_start(task.id, agent.id, task.type.value, task.input, task.correlation_id)
        except Exception as error:
            logger.warning("任务审计写入失败(start): %s", error)
        self._event_bus.emit(
            AgentEventType.TASK_ASSIGNED,
            {"taskId": task.id, "agentId": agent.id},
            task.correlation_id,
        )
        result = await agent.handle_task(task)

        event_type = (
            AgentEventType.TASK_COMPLETED if result.status == TaskStatus.COMPLETED else AgentEventType.TASK_FAILED
        )
        self._event_bus.emit(event_type, result, task.correlation_id, agent.id)
        try:
            end_status = AgentTaskStatus.COMPLETED if result.status == TaskStatus.COMPLETED else AgentTaskStatus.FAILED
            record_task_end(task.id, end_status, result.output)
        except Exception as error:
            logger.warning("任务审计写入失败(end): %s", error)
        return result

    async def broadcast_event(
        self,
        event_type: AgentEventType,
        payload: object,
        correlation_id: str | None = None,
    ) -> None:
        agents = list(self._agents.values())
        results = await asyncio.gather(
            *(
                agent.handle_event(new_event(event_type, payload, correlation_id, source="orchestrator"))
                for agent in agents
            ),
            return_exceptions=True,
        )
        for error in results:
            if isinstance(error, Exception):
                logger.warning("事件广播失败: %s", error)
