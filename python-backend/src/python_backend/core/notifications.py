"""通知组装(spec #9 A8/A9/A14):业务效果 → 通知载荷,经 WS 事件 notification.created 下发。

- 效果描述由 apply 处理器在事务内产生(纯数据),提交后由调用方 emit(回滚不误报)
- 文案零 LLM:订单状态映射表(宪章「客服通知文案沿用状态映射表」;#18 起为卖家运营口吻);
  库存五档文案与 check_inventory 工具共用同一函数(单一数据路径)
- 通知不落库:WS 推送 + 前端 localStorage(A14 语义)
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from python_backend.core.events import EventEmitter

# 订单状态 → 通知文案(#18:卖家运营口吻陈述句,无「您」买家视角;零 LLM 映射表)
ORDER_STATUS_MESSAGES: dict[str, str] = {
    "pending": "新订单 #{order_id} 已提交,待确认。",
    "confirmed": "订单 #{order_id} 已确认。",
    "processing": "订单 #{order_id} 进入处理中。",
    "shipped": "订单 #{order_id} 已发货。",
    "delivered": "订单 #{order_id} 已送达。",
    "cancelled": "订单 #{order_id} 已取消。",
    "returned": "订单 #{order_id} 已完成退货。",
}

KIND_ORDER_STATUS = "order_status"
KIND_INVENTORY_ALERT = "inventory_alert"
KIND_FX_MISSING = "fx_missing"

# 效果类型(apply 处理器返回的 effect["type"]):生产者与消费者共用常量,避免双名同概念
EFFECT_ORDER_STATUS = "order_status"
EFFECT_INVENTORY_LOW = "inventory_low"
EFFECT_FX_MISSING = "fx_missing"


def inventory_alert_message(title: str, current_stock: int, threshold: int) -> str:
    """五档库存文案(与 check_inventory 工具共用):售罄/严重不足/偏低/接近安全线/充足。"""
    ratio = current_stock / threshold
    if ratio <= 0:
        return f"🔴 {title} 已售罄!请立即补货。"
    if ratio < 0.3:
        return f"🟠 {title} 库存严重不足 (当前: {current_stock}, 安全线: {threshold})。建议3天内补货。"
    if ratio < 0.6:
        return f"🟡 {title} 库存偏低 (当前: {current_stock}, 安全线: {threshold})。建议7天内补货。"
    if ratio < 1:
        return f"🔵 {title} 库存接近安全线 (当前: {current_stock})。关注销量趋势。"
    return f"✅ {title} 库存充足 (当前: {current_stock})。"


def build_notifications(effects: list[dict], *, new_id: Callable[[], str] | None = None) -> list[dict]:
    """效果描述 → 通知载荷(纯函数;id 生成可注入以保测试确定性);未知效果忽略。"""
    make_id = new_id or (lambda: str(uuid.uuid4()))
    notifications: list[dict] = []
    for effect in effects:
        effect_type = effect.get("type")
        if effect_type == EFFECT_ORDER_STATUS:
            template = ORDER_STATUS_MESSAGES.get(effect.get("to") or "")
            if template is None:
                continue  # 未知状态不通知(防御:状态机外的值)
            notifications.append(
                _envelope(
                    make_id(),
                    template.format(order_id=effect["order_id"]),
                    KIND_ORDER_STATUS,
                    effect["order_id"],
                )
            )
        elif effect_type == EFFECT_INVENTORY_LOW:
            notifications.append(
                _envelope(
                    make_id(),
                    inventory_alert_message(effect["title"], effect["stock"], effect["threshold"]),
                    KIND_INVENTORY_ALERT,
                    None,
                )
            )
        elif effect_type == EFFECT_FX_MISSING:
            notifications.append(
                _envelope(
                    make_id(),
                    f"订单 #{effect['order_id']} 汇率快照缺失(汇率服务不可用),请人工核对金额。",
                    KIND_FX_MISSING,
                    effect["order_id"],
                )
            )
    return notifications


def _envelope(notification_id: str, message: str, kind: str, order_id: int | None) -> dict:
    """通知信封(契约 NotificationMessage):kind 供前端归组,orderId 供跳转定位。"""
    return {
        "notificationId": notification_id,
        "message": message,
        "kind": kind,
        "orderId": order_id,
        "timestamp": datetime.now(UTC).isoformat(),
    }


async def emit_notifications(emitter: EventEmitter, effects: list[dict]) -> None:
    """组装并广播通知(调用方须在事务提交后调用:效果已落库,通知才如实)。"""
    for payload in build_notifications(effects):
        await emitter.emit("notification.created", payload)
