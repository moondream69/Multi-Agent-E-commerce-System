"""审批中心 REST 路由:列表/决定/影子补执行。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from python_backend.core.approval import TOOL_REGISTRY
from python_backend.db import approval_repo
from python_backend.domain.events import AgentEventType


class DecideDto(BaseModel):
    approve: bool
    comment: str | None = None


def _serialize(row) -> dict:
    return {
        "id": str(row.id),
        "toolName": row.toolName,
        "params": row.params,
        "agentId": row.agentId,
        "taskId": row.taskId,
        "requestedBy": row.requestedBy,
        "status": row.status.value,
        "mode": row.mode,
        "result": row.result,
        "decidedBy": row.decidedBy,
        "decidedAt": row.decidedAt.isoformat() if row.decidedAt else None,
        "comment": row.comment,
        "createdAt": row.createdAt.isoformat(),
    }


def build_approvals_router(event_bus) -> APIRouter:
    router = APIRouter()

    @router.get("/api/approvals")
    def list_approvals(status: str | None = None) -> list[dict]:
        return [_serialize(row) for row in approval_repo.list_requests(status)]

    @router.post("/api/approvals/{request_id}/decide")
    async def decide(request: Request, request_id: str, dto: DecideDto) -> dict:
        username = request.state.username
        row = approval_repo.decide_request(request_id, dto.approve, username, dto.comment)
        if row is None:
            raise HTTPException(status_code=404, detail="审批请求不存在或已被处理")
        if event_bus is not None:
            event_bus.emit(
                AgentEventType.APPROVAL_DECIDED,
                {
                    "approvalId": request_id,
                    "toolName": row.toolName,
                    "approve": dto.approve,
                    "decidedBy": username,
                    "comment": dto.comment,
                },
                row.taskId,
                row.agentId or "system",
            )
        return _serialize(row)

    @router.post("/api/approvals/{request_id}/execute")
    async def execute_shadow(request: Request, request_id: str) -> dict:
        """影子建议一键补执行(按入参快照直调工具,无 LLM 上下文)。"""
        row = approval_repo.get_request(request_id)
        if row is None:
            raise HTTPException(status_code=404, detail="审批请求不存在")
        if row.mode != "shadow" or row.status.value != "shadow":
            raise HTTPException(status_code=400, detail="仅影子建议可补执行")
        tool = TOOL_REGISTRY.get(row.toolName)
        if tool is None:
            raise HTTPException(status_code=400, detail=f"工具 {row.toolName} 未注册")
        result = await tool.execute(row.params)
        approval_repo.mark_executed(request_id, request.state.username, result)
        return _serialize(approval_repo.get_request(request_id))

    return router
