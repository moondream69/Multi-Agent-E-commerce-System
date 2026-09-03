"""REST 路由(契约镜像 agent.controller.ts + dashboard.controller.ts)。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter

from python_backend.api.schemas import CreateTaskDto
from python_backend.api.serializers import agent_info, result_payload, to_json
from python_backend.core.orchestrator import Orchestrator
from python_backend.domain.agents import AgentStatus
from python_backend.domain.tasks import AgentTask, TaskType


def build_router(orchestrator: Orchestrator) -> APIRouter:
    router = APIRouter()

    @router.post("/api/agents/task")
    async def create_task(dto: CreateTaskDto) -> dict:
        task = AgentTask(
            id=str(uuid.uuid4()),
            type=TaskType(dto.type),
            input=dto.input,
            target_agent_id=dto.targetAgentId,
        )
        result = await orchestrator.route_task(task)
        return result_payload(result)

    @router.get("/api/agents/{agent_id}")
    async def get_agent(agent_id: str) -> dict:
        agent = orchestrator.get_agent(agent_id)
        if agent is None:
            return {"error": "Agent not found"}
        return {
            "id": agent.id,
            "name": agent.name,
            "status": agent.get_status().value,
            "tools": to_json(agent.get_tools()),
        }

    @router.get("/api/dashboard/agents")
    async def get_agents() -> list[dict]:
        return [agent_info(a) for a in orchestrator.get_registered_agents()]

    @router.get("/api/dashboard/status")
    async def get_status() -> dict:
        agents = orchestrator.get_registered_agents()
        online_agents = sum(1 for a in agents if a.get_status() in (AgentStatus.IDLE, AgentStatus.BUSY))
        return {
            "totalAgents": len(agents),
            "onlineAgents": online_agents,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    return router
