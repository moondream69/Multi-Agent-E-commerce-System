"""socketio 网关(契约镜像 agent.gateway.ts):chat:message → chat:response 三形状;agent:event 桥接。"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from pydantic import ValidationError

from python_backend.api.auth import verify_token
from python_backend.api.schemas import ChatMessagePayload
from python_backend.api.serializers import event_payload, to_json
from python_backend.core.event_bus import EventBus
from python_backend.core.intent_parser import IntentParser
from python_backend.core.orchestrator import Orchestrator
from python_backend.core.output_text import extract_output_text
from python_backend.db.conversation_repo import append_message
from python_backend.domain.events import AgentEventType
from python_backend.domain.tasks import AgentTask

__all__ = ["bridge_all_events", "bridge_notifications", "register_ws_handlers"]

logger = logging.getLogger(__name__)

# 固定演示买家(与 store.py 的下单买家一致)
DEMO_BUYER_ID = "demo-buyer"

# sid → 登录用户名(单进程内存映射;多 worker 部署时此映射与 EventBus 一样会断,架构约束)
_SID_USERS: dict[str, str] = {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def register_ws_handlers(
    sio,
    orchestrator: Orchestrator,
    intent_parser: IntentParser,
) -> None:
    @sio.event
    async def connect(sid: str, environ: dict, auth: dict | None = None) -> None:
        token = (auth or {}).get("token") if isinstance(auth, dict) else None
        username = verify_token(token) if token else None
        if username is None:
            raise ConnectionRefusedError("认证失败")
        _SID_USERS[sid] = username
        logger.info("客户端已连接: %s (%s)", sid, username)

    @sio.event
    async def disconnect(sid: str) -> None:
        _SID_USERS.pop(sid, None)
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
        username = _SID_USERS.get(sid, DEMO_BUYER_ID)
        parsed_intent = intent_parser.parse(text)
        task = AgentTask(
            id=task_id,
            type=parsed_intent.task_type,
            input={**parsed_intent.extracted_input, "originalText": text},
            requested_by=None if username == DEMO_BUYER_ID else username,
        )

        try:
            append_message(username, "user", text)
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
                    username,
                    "assistant",
                    extract_output_text(result.output),
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
    """客服主动通知(customer.notification)桥接为 client 侧的 chat:notification(按 kind 归类的 Agent 卡片铃铛)。"""

    async def _bridge(event) -> None:
        payload = event.payload or {}
        await sio.emit(
            "chat:notification",
            {
                "type": "chat:notification",
                "notificationId": event.id,
                "message": payload.get("message") or "",
                "agentId": payload.get("agentId") or "customer-service",
                "kind": payload.get("kind") or "",
                "orderId": payload.get("orderId"),
                "timestamp": to_json(event.timestamp),
            },
        )

    event_bus.on(AgentEventType.CUSTOMER_NOTIFICATION, _bridge)
