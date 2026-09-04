"""socketio 网关(契约镜像 agent.gateway.ts):chat:message → chat:response 三形状;agent:event 桥接。"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from pydantic import ValidationError

from python_backend.api.schemas import ChatMessagePayload
from python_backend.api.serializers import event_payload, to_json
from python_backend.core.event_bus import EventBus
from python_backend.core.intent_parser import IntentParser
from python_backend.core.orchestrator import Orchestrator
from python_backend.db.conversation_repo import append_message
from python_backend.domain.events import AgentEventType
from python_backend.domain.tasks import AgentTask

__all__ = ["bridge_all_events", "bridge_notifications", "register_ws_handlers"]

logger = logging.getLogger(__name__)

# 固定演示买家(与 store.py 的下单买家一致)
DEMO_BUYER_ID = "demo-buyer"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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

        try:
            append_message(DEMO_BUYER_ID, "user", text)
        except Exception as error:
            logger.warning("保存用户消息失败: %s", error)

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
            try:
                append_message(
                    DEMO_BUYER_ID,
                    "assistant",
                    json.dumps(result.output, ensure_ascii=False),
                    agent_id=result.agent_id,
                    task_id=task.id,
                )
            except Exception as error:
                logger.warning("保存助手消息失败: %s", error)
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


def bridge_notifications(sio, event_bus: EventBus) -> None:
    """客服主动通知(customer.notification)桥接为 client 侧的 chat:notification(买家视角聊天面板)。"""

    async def _bridge(event) -> None:
        payload = event.payload or {}
        await sio.emit(
            "chat:notification",
            {
                "type": "chat:notification",
                "notificationId": event.id,
                "message": payload.get("message") or "",
                "agentId": payload.get("agentId") or "customer-service",
                "orderId": payload.get("orderId"),
                "timestamp": to_json(event.timestamp),
            },
        )

    event_bus.on(AgentEventType.CUSTOMER_NOTIFICATION, _bridge)
