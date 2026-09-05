"""REST 路由(契约镜像 agent.controller.ts + dashboard.controller.ts)。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from sqlalchemy import select

from python_backend.api.schemas import CreateTaskDto
from python_backend.api.serializers import agent_info, result_payload, to_json
from python_backend.core.orchestrator import Orchestrator
from python_backend.db.models import Conversation
from python_backend.db.session import SessionLocal
from python_backend.domain.agents import AgentStatus
from python_backend.domain.tasks import AgentTask, TaskType


def build_router(orchestrator: Orchestrator) -> APIRouter:
    router = APIRouter()

    @router.post("/api/agents/task")
    async def create_task(request: Request, dto: CreateTaskDto) -> dict:
        task = AgentTask(
            id=str(uuid.uuid4()),
            type=TaskType(dto.type),
            input=dto.input,
            target_agent_id=dto.targetAgentId,
            requested_by=getattr(request.state, "username", None),
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

    @router.get("/api/conversations")
    async def get_conversation(request: Request) -> dict:
        """当前登录用户的聊天历史(按会话归属人隔离;兼容旧数据回落到演示买家)。"""
        username = getattr(request.state, "username", None) or "demo-buyer"
        with SessionLocal() as session:
            row = session.scalar(select(Conversation).where(Conversation.customerId == username))
            return {"customerId": username, "messages": list(row.messages or []) if row else []}

    return router
