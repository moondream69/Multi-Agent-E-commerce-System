"""事件类型(镜像 src/common/interfaces/event.interface.ts)。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class AgentEventType(StrEnum):
    REPORT_GENERATED = "report.generated"
    PRODUCT_CREATED = "product.created"
    PRODUCT_UPDATED = "product.updated"
    ORDER_STATUS_CHANGED = "order.status_changed"
    REPLY_GENERATED = "reply.generated"
    ESCALATION_TRIGGERED = "escalation.triggered"
    TASK_ASSIGNED = "task.assigned"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    AGENT_STATUS_CHANGED = "agent.status_changed"


@dataclass
class AgentEvent:
    id: str
    type: AgentEventType
    source: str
    timestamp: datetime
    payload: Any
    correlation_id: str | None = None
