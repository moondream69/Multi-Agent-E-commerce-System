"""进程内异步事件总线(asyncio 实现,等价于 @nestjs/event-emitter 的 EventEmitter2)。

emit() 是同步入口:同步 handler 直接执行,协程 handler 用 create_task 调度。
broadcast() 是异步入口:逐个 await,失败仅告警不中断(与 NestJS 版一致)。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from python_backend.domain.events import AgentEvent, AgentEventType

logger = logging.getLogger(__name__)

EventHandler = Callable[[AgentEvent], Awaitable[None] | None]


def new_event(
    type_: AgentEventType,
    payload: object,
    correlation_id: str | None = None,
    source: str = "system",
) -> AgentEvent:
    return AgentEvent(
        id=str(uuid.uuid4()),
        type=type_,
        source=source,
        timestamp=datetime.now(UTC),
        payload=payload,
        correlation_id=correlation_id,
    )


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._pending_tasks: set[asyncio.Task] = set()

    def on(self, type_: AgentEventType, handler: EventHandler) -> None:
        self._handlers[type_.value].append(handler)

    def emit(
        self,
        type_: AgentEventType,
        payload: object,
        correlation_id: str | None = None,
        source: str = "system",
    ) -> None:
        event = new_event(type_, payload, correlation_id, source)
        for handler in list(self._handlers[type_.value]):
            result = handler(event)
            if inspect.isawaitable(result):
                task = asyncio.ensure_future(result)
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)

    async def broadcast(
        self,
        type_: AgentEventType,
        payload: object,
        handlers: list[EventHandler],
        source: str = "system",
        correlation_id: str | None = None,
    ) -> None:
        event = new_event(type_, payload, correlation_id, source)
        for handler in handlers:
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as error:
                logger.warning("事件处理器失败: %s", error)
