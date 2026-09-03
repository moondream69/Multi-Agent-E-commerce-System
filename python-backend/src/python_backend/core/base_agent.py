"""BaseAgent:状态机 + 模板方法(镜像 src/core/agent-base/base-agent.ts)。

- 状态流转:idle/busy/error/offline,仅在变化时发 agent.status_changed(与 TS 一致)
- 并发:activeTaskIds 集合,最后一个任务完成才回到 idle
- 子类只需声明 id/name/description/system_prompt + tools,并实现 handle_event
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from python_backend.core.event_bus import EventBus
from python_backend.core.graph import AgentState, build_react_graph
from python_backend.domain.agents import AgentStatus
from python_backend.domain.events import AgentEvent, AgentEventType
from python_backend.domain.tasks import (
    AgentResult,
    AgentTask,
    TaskStatus,
    ToolDefinition,
)
from python_backend.domain.tools import ToolProtocol
from python_backend.infrastructure.llm import LlmService

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class BaseAgent:
    id: str = ""
    name: str = ""
    description: str = ""
    system_prompt: str = ""

    def __init__(self, event_bus: EventBus, llm: LlmService) -> None:
        self._event_bus = event_bus
        self._llm = llm
        self._status = AgentStatus.IDLE
        self._active_task_ids: set[str] = set()
        self._task_steps: dict[str, list[dict[str, Any]]] = {}
        self.tools: list[ToolProtocol] = []
        self._graph = None

    async def handle_task(self, task: AgentTask) -> AgentResult:
        self._active_task_ids.add(task.id)
        self._set_status(AgentStatus.BUSY, task)

        try:
            self._add_step(task.id, "start", TaskStatus.COMPLETED, f"Agent {self.name} 开始处理")
            output = await self.execute_task(task)
            self._add_step(task.id, "done", TaskStatus.COMPLETED, "任务执行完成")
            self._active_task_ids.discard(task.id)
            if not self._active_task_ids:
                self._set_status(AgentStatus.IDLE, task)
            return AgentResult(
                task_id=task.id,
                agent_id=self.id,
                status=TaskStatus.COMPLETED,
                output=output,
                steps=list(self._task_steps.get(task.id, [])),
                completed_at=_now(),
            )
        except Exception as error:
            self._active_task_ids.discard(task.id)
            if not self._active_task_ids:
                self._set_status(AgentStatus.ERROR, task)
            self._add_step(task.id, "error", TaskStatus.FAILED, str(error))
            return AgentResult(
                task_id=task.id,
                agent_id=self.id,
                status=TaskStatus.FAILED,
                output={"error": str(error)},
                steps=list(self._task_steps.get(task.id, [])),
                completed_at=_now(),
            )
        finally:
            self._task_steps.pop(task.id, None)

    def get_status(self) -> AgentStatus:
        return self._status

    def get_tools(self) -> list[ToolDefinition]:
        return [tool.definition for tool in self.tools]

    async def handle_event(self, event: AgentEvent) -> None:
        logger.info("收到事件: %s from %s", event.type, event.source)

    def _set_status(self, status: AgentStatus, task: AgentTask) -> None:
        if self._status == status:
            return
        self._status = status
        self._event_bus.emit(
            AgentEventType.AGENT_STATUS_CHANGED,
            {"agentId": self.id, "status": status.value, "taskId": task.id},
            task.correlation_id,
            self.id,
        )

    def _add_step(self, task_id: str, name: str, status: TaskStatus, detail: str) -> None:
        steps = self._task_steps.setdefault(task_id, [])
        steps.append(
            {
                "name": name,
                "status": status.value,
                "detail": detail,
                "startedAt": datetime.now(UTC),
                "completedAt": datetime.now(UTC) if status == TaskStatus.COMPLETED else None,
            }
        )

    async def execute_task(self, task: AgentTask) -> dict[str, Any]:
        if self._graph is None:
            self._graph = build_react_graph(self.system_prompt, self.tools, self._llm)
        state: AgentState = {
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": json.dumps(task.input, ensure_ascii=False)},
            ],
            "steps": [],
            "round": 0,
            "result": None,
        }
        end = await self._graph.ainvoke(state)
        self._task_steps[task.id] = end["steps"]
        return end["result"] or {}
