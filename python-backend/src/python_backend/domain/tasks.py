"""任务/结果/工具类型(镜像 src/common/interfaces/task.interface.ts)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class TaskType(StrEnum):
    PRODUCT_RESEARCH = "product_research"
    ORDER_MANAGEMENT = "order_management"
    CUSTOMER_SERVICE = "customer_service"


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


def _now() -> datetime:
    return datetime.now().astimezone()


@dataclass
class AgentTask:
    id: str
    type: TaskType
    input: dict[str, Any]
    target_agent_id: str | None = None
    correlation_id: str | None = None
    created_at: datetime = field(default_factory=_now)


@dataclass
class AgentResult:
    task_id: str
    agent_id: str
    status: TaskStatus
    output: dict[str, Any]
    steps: list[dict[str, Any]]
    completed_at: datetime


@dataclass
class ToolParameter:
    name: str
    type: str  # 'string' | 'number' | 'boolean' | 'object' | 'array'
    description: str
    required: bool = True


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)
