"""socketio 网关(契约镜像 agent.gateway.ts):chat:message → chat:response 三形状;agent:event 桥接。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from pydantic import ValidationError

from python_backend.api.schemas import ChatMessagePayload
from python_backend.api.serializers import event_payload, to_json
from python_backend.core.event_bus import EventBus
from python_backend.core.intent_parser import IntentParser
from python_backend.core.orchestrator import Orchestrator
from python_backend.domain.events import AgentEventType
from python_backend.domain.tasks import AgentTask

__all__ = ["register_ws_handlers", "bridge_all_events"]

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_ws_handlers(
    sio,
    orchestrator: Orchestrator,
    intent_parser: IntentParser,
) -> None:
    @sio.event
    async def connect(sid: str, environ: dict) -> None:
        logger.info("客户端已连接: %s", sid)

    @sio.event
    async def disconnect(sid: str) -> None:
        logger.info("客户端已断开: %s", sid)

    @sio.on("chat:message")
    async def handle_chat_message(sid: str, payload: dict) -> None:
        task_id = str(uuid.uuid4())
        try:
            parsed = ChatMessagePayload(**payload)
        except ValidationError as error:
            await sio.emit(
                "chat:response",
                {
                    "type": "task_error",
                    "taskId": task_id,
                    "error": f"消息格式错误: {error.errors()[0]['msg']}",
                    "timestamp": _now_iso(),
                },
                to=sid,
            )
            return

        text = parsed.text
        logger.info("收到聊天消息: %s...", text[:50])
        parsed_intent = intent_parser.parse(text)
        task = AgentTask(
            id=task_id,
            type=parsed_intent.task_type,
            input={**parsed_intent.extracted_input, "originalText": text},
        )

        await sio.emit(
            "chat:response",
            {
                "type": "task_created",
                "taskId": task.id,
                "taskType": task.type.value,
                "text": text,
                "timestamp": _now_iso(),
            },
            to=sid,
        )

        try:
            result = await orchestrator.route_task(task)
            await sio.emit(
                "chat:response",
                {
                    "type": "task_result",
                    "taskId": task.id,
                    "agentId": result.agent_id,
                    "status": result.status.value,
                    "output": to_json(result.output),
                    "steps": to_json(result.steps),
                    "timestamp": _now_iso(),
                },
                to=sid,
            )
        except Exception as error:
            await sio.emit(
                "chat:response",
                {
                    "type": "task_error",
                    "taskId": task.id,
                    "error": str(error),
                    "timestamp": _now_iso(),
                },
                to=sid,
            )


def bridge_all_events(sio, event_bus: EventBus) -> None:
    """把所有领域事件桥接为 client 侧的 agent:event(对应 TS onModuleInit 的循环注册)。"""

    async def _bridge(event) -> None:
        await sio.emit("agent:event", event_payload(event))

    for event_type in AgentEventType:
        event_bus.on(event_type, _bridge)
