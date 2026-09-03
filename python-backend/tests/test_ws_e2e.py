"""端到端:启动真实 uvicorn,用 socket.io 客户端走 chat:message → chat:response 全链路。

与开发环境相同的依赖:Postgres/Redis/Ollama/DeepSeek。消息将真实验证三形状与 agent:event 桥接。
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pytest
import requests
import socketio

PYTHON_BACKEND = Path(__file__).resolve().parents[1]
PYTHON = PYTHON_BACKEND / ".venv" / "Scripts" / "python.exe"
PORT = 3000
BASE_URL = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def server():
    log_file = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    # 直接用 .venv 的 python(uv run 会产生包装进程,terminate 不杀 uvicorn 子进程导致端口残留)
    proc = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "python_backend.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(PYTHON_BACKEND),
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 30
    ready = False
    while time.time() < deadline and proc.poll() is None:
        try:
            if requests.get(f"{BASE_URL}/health", timeout=1).ok:
                ready = True
                break
        except Exception:
            time.sleep(0.5)
    if not ready:
        proc.kill()
        with open(log_file.name, encoding="utf-8", errors="replace") as fh:
            pytest.fail("服务器启动失败:\n" + fh.read()[-2000:])
    yield proc
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    log_file.close()


async def test_chat_message_full_flow(server):
    client = socketio.AsyncClient()
    events: list[dict] = []
    agent_events: list[dict] = []
    result_event = asyncio.Event()
    assigned_event = asyncio.Event()

    def on_response(data: dict) -> None:
        events.append(data)
        if data.get("type") == "task_result":
            result_event.set()

    def on_agent(data: dict) -> None:
        agent_events.append(data)
        if data.get("type") == "task.assigned":
            assigned_event.set()

    client.on("chat:response", on_response)
    client.on("agent:event", on_agent)

    await client.connect(BASE_URL, wait_timeout=15)
    task_text = f"分析蓝牙耳机市场趋势-{uuid.uuid4().hex[:4]}"
    await client.emit("chat:message", {"text": task_text})
    await asyncio.wait_for(result_event.wait(), timeout=240)
    # agent:event 桥接由事件总线异步分派,允许其在 task_result 之后片刻到达
    try:
        await asyncio.wait_for(assigned_event.wait(), timeout=5)
    except asyncio.TimeoutError:
        pytest.fail(f"未收到 task.assigned 桥接事件,已收 agent:event: {[e['type'] for e in agent_events]}")
    await client.disconnect()

    created = [e for e in events if e["type"] == "task_created"]
    result = next(e for e in events if e["type"] == "task_result")

    # 三形状之 task_created:与 task_result 共享 taskId
    assert created and created[0]["taskId"] == result["taskId"]
    assert set(created[0].keys()) == {"type", "taskId", "taskType", "text", "timestamp"}

    # task_result 形状(对照 frontend/src/types/events.ts)
    assert set(result.keys()) == {"type", "taskId", "agentId", "status", "output", "steps", "timestamp"}
    assert result["agentId"] == "product-research"
    assert result["status"] == "completed"

    # agent:event 桥接(至少收到 task.assigned)
    assert any(e["type"] == "task.assigned" for e in agent_events)


async def test_invalid_message_gets_task_error(server):
    client = socketio.AsyncClient()
    error_event = asyncio.Event()
    responses: list[dict] = []

    def on_response(data: dict) -> None:
        responses.append(data)
        if data.get("type") == "task_error":
            error_event.set()

    client.on("chat:response", on_response)
    await client.connect(BASE_URL, wait_timeout=15)
    await client.emit("chat:message", {"text": ""})
    await asyncio.wait_for(error_event.wait(), timeout=15)
    await client.disconnect()

    assert responses[-1]["type"] == "task_error"
    assert "格式错误" in responses[-1]["error"]
