"""事件发射器协议(spec #7:WS 实时通道)。

图节点与 REST 层依赖此协议发出业务事件;生产实现为 socket.io 广播(api/ws),
测试注入内存记录器。事件形状以 frontend/src/types/events.ts 契约为准。
"""

from __future__ import annotations

from typing import Protocol


class EventEmitter(Protocol):
    """业务事件发射:emit(event, payload),event 为契约事件名(如 approval.requested)。"""

    async def emit(self, event: str, payload: dict) -> None: ...


class NullEmitter:
    """无 WS 装配时的 no-op 发射(单测/简化装配)。"""

    async def emit(self, event: str, payload: dict) -> None:
        return None
