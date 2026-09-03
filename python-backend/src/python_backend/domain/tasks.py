"""任务/结果/工具类型(镜像 src/common/interfaces/task.interface.ts)。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class TaskType(str, enum.Enum):
    PRODUCT_RESEARCH = "product_research"
    ORDER_MANAGEMENT = "order_management"
    CUSTOMER_SERVICE = "customer_service"


class TaskStatus(str, enum.Enum):
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
class TaskStep:
    name: str
    status: TaskStatus
    detail: str
    started_at: datetime
    completed_at: datetime | None = None


@dataclass
class AgentResult:
    task_id: str
    agent_id: str
    status: TaskStatus
    output: dict[str, Any]
    steps: list[TaskStep]
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
