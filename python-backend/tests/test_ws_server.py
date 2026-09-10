"""WS e2e(spec #7):socket.io 真实连接收事件(本机 uvicorn 临时端口,无外部依赖)。

验证 SocketEmitter → AsyncServer → ASGIApp → 客户端 全链路;
图形与 REST 事件内容断言见 test_ws_events.py(协议层,更细粒度)。
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading

import httpx
import pytest
import socketio
import uvicorn
from langgraph.checkpoint.memory import InMemorySaver

from python_backend.api.app import create_app
from python_backend.api.ws import SocketEmitter, build_socketio, wrap_with_socketio
from python_backend.core.graph import build_supervisor
from tests.conftest import FakeApply, InMemoryApprovalBatchStore, StubPlanner, slice_agent

PUBLISH = {
    "action": "product.publish",
    "params": {"product_id": 1},
    "snapshot": {"exists": True, "status": "draft"},
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server_url():
    """后台线程起真实 uvicorn(socketio ASGIApp),返回 base url。"""
    store = InMemoryApprovalBatchStore()
    sio = build_socketio(["http://localhost"])
    emitter = SocketEmitter(sio)
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent([], actions=[PUBLISH], answer="已登记")},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=FakeApply(),
        emitter=emitter,
    )
    app = create_app(graph=graph, batch_store=store, apply_fn=FakeApply(), emitter=emitter, auth_required=False)
    wrapped = wrap_with_socketio(app, sio)

    port = _free_port()
    # loop="none":禁掉 uvicorn 自身的循环管理,由服务线程内 asyncio.run 的 loop_factory 建循环
    server = uvicorn.Server(uvicorn.Config(wrapped, host="127.0.0.1", port=port, log_level="warning", loop="none"))

    def run() -> None:
        # 服务线程显式以 SelectorEventLoop 建循环:psycopg 异步处理器要求 Selector,
        # 且不受主线程(pytest-asyncio)事件循环策略影响(与 run.py 同一 loop_factory 手法)
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.integration  # 端点写任务行(PG),离线不可跑(issue #13)
@pytest.mark.usefixtures("requires_postgres")
async def test_socketio_client_receives_approval_requested(server_url: str) -> None:
    """e2e:REST 发起任务 → WS 广播 approval.requested 到达真实 socket.io 客户端。"""
    received: list[dict] = []
    client = socketio.AsyncClient()
    await client.connect(server_url)
    client.on("approval.requested", lambda payload: received.append(payload))

    async with httpx.AsyncClient(base_url=server_url) as http:
        response = await http.post("/api/tasks", json={"request": "上架商品"})
        assert response.json()["status"] == "interrupted"

    # 广播到达(轮询等待,避免对事件循环竞态的硬编码等待)
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.05)

    await client.disconnect()
    assert len(received) == 1
    assert received[0]["sliceNo"] == 1
    assert received[0]["batches"][0]["actionType"] == "product.publish"


async def test_socketio_rest_coexist_on_same_port(server_url: str) -> None:
    """REST 与 WS 同端口共存(socketio ASGIApp 外挂 FastAPI)。"""
    async with httpx.AsyncClient(base_url=server_url) as http:
        response = await http.get("/api/approvals")
        assert response.status_code == 200
