"""契约序列化:信封字段一律驼峰(与前端类型文件一致),payload/工具结果原样透传。

契约真源: frontend/src/types/events.ts(OpenAPI 自动生成前的唯一权威)。
注意:域模型内部用 snake_case;只有信封(AgentEvent/AgentResult)在此显式映射为驼峰。
"""

from __future__ import annotations

from typing import Any

from python_backend.db.rows import plain
from python_backend.domain.agents import AgentProtocol
from python_backend.domain.events import AgentEvent
from python_backend.domain.tasks import AgentResult


def to_json(value: Any) -> Any:
    """枚举/时间/Decimal/UUID → JSON 友好值(不改变 dict 键)。"""
    return plain(value)


def agent_info(agent: AgentProtocol) -> dict[str, Any]:
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "status": agent.get_status().value,
        "tools": to_json(agent.get_tools()),
    }


def result_payload(result: AgentResult) -> dict[str, Any]:
    return {
        "taskId": result.task_id,
        "agentId": result.agent_id,
        "status": result.status.value,
        "output": to_json(result.output),
        "steps": to_json(result.steps),
        "completedAt": to_json(result.completed_at),
    }


def event_payload(event: AgentEvent) -> dict[str, Any]:
    """agent:event 信封,与事件类型一致(events.ts 的 AgentEvent)。"""
    return {
        "id": event.id,
        "type": event.type.value,
        "source": event.source,
        "timestamp": to_json(event.timestamp),
        "payload": to_json(event.payload),
        "correlationId": event.correlation_id,
    }
