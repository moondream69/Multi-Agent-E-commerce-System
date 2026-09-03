"""Agent 协议(镜像 src/common/interfaces/agent.interface.ts)。"""

from __future__ import annotations

import enum
from typing import Protocol

from python_backend.domain.events import AgentEvent
from python_backend.domain.tasks import AgentResult, AgentTask, ToolDefinition


class AgentStatus(str, enum.Enum):
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"


class AgentProtocol(Protocol):
    id: str
    name: str
    description: str

    async def handle_task(self, task: AgentTask) -> AgentResult: ...

    async def handle_event(self, event: AgentEvent) -> None: ...

    def get_status(self) -> AgentStatus: ...

    def get_tools(self) -> list[ToolDefinition]: ...
