"""分级审批护栏:风险分级 + 守卫执行 + 工具注册表。

- auto:低风险,直接执行
- approve:高风险(订单状态流转/商品上架等),插审批行 + 事件 + 轮询等待人工决定
- shadow:影子模式(settings.shadow_mode 开启),高危建议只记录不执行,卖家在审批中心一键补执行

取舍:审批等待是进程内轮询(单 uvicorn worker 是架构硬约束),重启丢失在途等待;
升级路径:审批表与 graph 解耦,将来可换 LangGraph interrupt(),审批表直接复用为 UI 数据源。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from python_backend.db import approval_repo
from python_backend.domain.events import AgentEventType
from python_backend.domain.tools import ToolProtocol
from python_backend.settings import settings

logger = logging.getLogger(__name__)

# 工具风险分级:返回 "approve"(需人工审批)或 "auto"(自动执行)。
# 缺省 auto;工具不在 tool_map 中时根本到不了执行点(graph.py 已拦截),fail-open 可接受。
# 预留:未来新增 updatePrice 等 action 时在此加一行规则。
RISK_RULES: dict[str, Callable[[dict[str, Any]], str]] = {
    "product_crud": lambda p: "approve" if p.get("action") == "updateStatus" else "auto",
    "order_workflow": lambda p: "approve" if p.get("action") == "transition" else "auto",
}

# 工具实例注册表(影子建议补执行 + 审计用),应用启动时注册
TOOL_REGISTRY: dict[str, ToolProtocol] = {}

POLL_INTERVAL_SECONDS = 1.5


def register_tool(tool: ToolProtocol) -> None:
    TOOL_REGISTRY[tool.definition.name] = tool


def risk_level(tool_name: str, params: dict[str, Any]) -> str:
    rule = RISK_RULES.get(tool_name)
    return rule(params) if rule else "auto"


class GuardContext:
    """守卫执行上下文:task_id/agent_id/requested_by 溯源,event_bus 通知。"""

    def __init__(
        self,
        task_id: str | None,
        agent_id: str | None,
        requested_by: str | None,
        event_bus,
    ) -> None:
        self.task_id = task_id
        self.agent_id = agent_id
        self.requested_by = requested_by
        self.event_bus = event_bus


async def execute_guarded(
    tool_name: str,
    params: dict[str, Any],
    execute_fn: Callable[[], Awaitable[Any]],
    ctx: GuardContext,
    ttl_seconds: float | None = None,
) -> Any:
    """按风险分级执行工具。approve 分支轮询审批表至 decided/expired。"""
    level = risk_level(tool_name, params)
    if level == "auto":
        return await execute_fn()

    if settings.shadow_mode:
        request_id = approval_repo.create_request(
            tool_name, params, ctx.agent_id, ctx.task_id, ctx.requested_by, mode="shadow"
        )
        if ctx.event_bus is not None:
            ctx.event_bus.emit(
                AgentEventType.APPROVAL_REQUESTED,
                {"approvalId": request_id, "toolName": tool_name, "params": params, "mode": "shadow"},
                ctx.task_id,
                ctx.agent_id or "system",
            )
        logger.info("影子建议已记录(未执行): %s %s", tool_name, params)
        return {"warning": f"已记录影子建议(未执行),可在审批中心一键执行。建议编号: {request_id}"}

    request_id = approval_repo.create_request(
        tool_name, params, ctx.agent_id, ctx.task_id, ctx.requested_by, mode="approval"
    )
    if ctx.event_bus is not None:
        ctx.event_bus.emit(
            AgentEventType.APPROVAL_REQUESTED,
            {"approvalId": request_id, "toolName": tool_name, "params": params, "mode": "approval"},
            ctx.task_id,
            ctx.agent_id or "system",
        )
    logger.info("高危操作待审批: %s %s (request=%s)", tool_name, params, request_id)

    deadline = time.monotonic() + (ttl_seconds or settings.approval_ttl_hours * 3600)
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        row = approval_repo.get_request(request_id)
        if row is None:
            break
        if row.status.value == "approved":
            result = await execute_fn()
            approval_repo.mark_result(request_id, result)
            logger.info("审批通过已执行: %s (request=%s)", tool_name, request_id)
            return result
        if row.status.value == "rejected":
            return {"error": f"该操作已被 {row.decidedBy} 拒绝: {row.comment or '未附原因'}"}
        if row.status.value == "expired":
            return {"error": "审批已超时,操作未执行,请重新发起"}

    approval_repo.expire_one(request_id)  # 兜底:进程内 TTL 耗尽后行仍未决定
    return {"error": "审批超时未决定,操作未执行"}
