"""WS 实时通道(spec #7):socket.io 广播 + 发射器实现。

事件(契约 events.ts):approval.requested / approval.decided / task.created /
task.interrupted / task.completed / task.failed。局域网小团队规模,广播 + 客户端按
threadId 过滤,不建房间。
"""

from __future__ import annotations

import socketio

from python_backend.core.events import EventEmitter


def build_socketio(cors_origins: list[str]) -> socketio.AsyncServer:
    return socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=cors_origins)


class SocketEmitter(EventEmitter):
    """socket.io 广播发射器(AsyncServer.emit 须在事件循环内调用)。"""

    def __init__(self, server: socketio.AsyncServer) -> None:
        self._server = server

    async def emit(self, event: str, payload: dict) -> None:
        await self._server.emit(event, payload)


def wrap_with_socketio(app, server: socketio.AsyncServer) -> socketio.ASGIApp:
    """FastAPI 应用外挂 socket.io ASGI(uvicorn 以返回的应用为入口)。"""
    return socketio.ASGIApp(server, other_asgi_app=app)
