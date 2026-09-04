"""订单处理 Agent 的 4 个工具(镜像 src/agents/order-management/tools/*.tool.ts)。"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any, ClassVar

from python_backend.core.event_bus import EventBus
from python_backend.db.models import Order, OrderStatus, Product
from python_backend.db.rows import row_to_dict
from python_backend.db.session import SessionLocal
from python_backend.domain.events import AgentEventType
from python_backend.domain.tasks import ToolDefinition, ToolParameter

logger = logging.getLogger(__name__)


def _uuid(value: str) -> uuid.UUID:
    return uuid.UUID(value)


class ProductCrudTool:
    definition = ToolDefinition(
        name="product_crud",
        description="商品增删改查,支持创建、查询、更新状态等操作",
        parameters=[
            ToolParameter(
                "action",
                "string",
                "操作类型: create|listByCategory|findBySku|updateStatus",
                required=True,
            ),
            ToolParameter("sku", "string", "商品 SKU (create/findBySku 时使用)", required=False),
            ToolParameter("title", "string", "商品标题 (create 时使用)", required=False),
            ToolParameter("price", "number", "商品价格 (create 时使用)", required=False),
            ToolParameter("category", "string", "品类名称 (create/listByCategory 时使用)", required=False),
            ToolParameter("description", "string", "商品描述 (create 时使用)", required=False),
            ToolParameter("id", "string", "商品 ID (updateStatus 时使用)", required=False),
            ToolParameter("status", "string", "商品状态 (updateStatus 时使用)", required=False),
        ],
    )

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus

    def create(self, sku: str, title: str, price: float, category: str, description: str | None = None) -> dict:
        with SessionLocal() as session:
            product = Product(
                sku=sku,
                title=title,
                price=Decimal(str(price)),
                category=category,
                description=description or "",
            )
            session.add(product)
            session.commit()
            session.refresh(product)
            result = row_to_dict(product)
        if self._event_bus is not None:
            self._event_bus.emit(AgentEventType.PRODUCT_CREATED, {"product": result}, source="order-management")
        return result

    def find_by_sku(self, sku: str) -> dict | None:
        with SessionLocal() as session:
            product = session.query(Product).where(Product.sku == sku).first()
            return row_to_dict(product) if product else None

    def list_by_category(self, category: str) -> list[dict]:
        with SessionLocal() as session:
            products = session.query(Product).where(Product.category == category).all()
            return [row_to_dict(p) for p in products]

    def update_status(self, product_id: str, status: str) -> None:
        with SessionLocal() as session:
            product = session.get(Product, _uuid(product_id))
            if product is None:
                raise ValueError(f"商品 {product_id} 未找到")
            product.status = status
            session.commit()
        if self._event_bus is not None:
            self._event_bus.emit(
                AgentEventType.PRODUCT_UPDATED,
                {"productId": product_id, "status": status},
                source="order-management",
            )

    async def execute(self, params: dict[str, Any]) -> Any:
        action = params["action"]
        if action == "create":
            return self.create(
                params["sku"],
                params["title"],
                float(params["price"]),
                params["category"],
                params.get("description"),
            )
        if action == "listByCategory":
            return self.list_by_category(params["category"])
        if action == "findBySku":
            return self.find_by_sku(params["sku"])
        if action == "updateStatus":
            self.update_status(params["id"], params["status"])
            return {"success": True}
        raise ValueError(f"未知 action: {action}")


class OrderWorkflowTool:
    definition = ToolDefinition(
        name="order_workflow",
        description="订单工作流管理,支持创建订单、状态流转和按状态查询",
        parameters=[
            ToolParameter("action", "string", "操作类型: create|transition|listByStatus", required=True),
            ToolParameter("productId", "string", "商品 ID (create 时使用)", required=False),
            ToolParameter("totalAmount", "number", "订单总金额 (create 时使用)", required=False),
            ToolParameter("customerId", "string", "客户 ID (create 时使用)", required=False),
            ToolParameter("orderId", "string", "订单 ID (transition 时使用)", required=False),
            ToolParameter("newStatus", "string", "目标订单状态 (transition 时使用)", required=False),
            ToolParameter("status", "string", "订单状态 (listByStatus 时使用)", required=False),
        ],
    )

    VALID_TRANSITIONS: ClassVar[dict[OrderStatus, list[OrderStatus]]] = {
        OrderStatus.PENDING: [OrderStatus.CONFIRMED, OrderStatus.CANCELLED],
        OrderStatus.CONFIRMED: [OrderStatus.PROCESSING, OrderStatus.CANCELLED],
        OrderStatus.PROCESSING: [OrderStatus.SHIPPED],
        OrderStatus.SHIPPED: [OrderStatus.DELIVERED],
        OrderStatus.DELIVERED: [OrderStatus.RETURNED],
        OrderStatus.CANCELLED: [],
        OrderStatus.RETURNED: [],
    }

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus

    def _emit_status_changed(
        self,
        order_id: str,
        from_status: str | None,
        to_status: str,
        product_id: str,
        total_amount: float,
    ) -> None:
        if self._event_bus is None:
            return
        self._event_bus.emit(
            AgentEventType.ORDER_STATUS_CHANGED,
            {
                "orderId": order_id,
                "from": from_status,
                "to": to_status,
                "productId": product_id,
                "totalAmount": total_amount,
            },
            source="order-management",
        )

    def create(self, product_id: str, total_amount: float, customer_id: str | None = None) -> dict:
        with SessionLocal() as session:
            order = Order(
                product_id=_uuid(product_id),
                customer_id=_uuid(customer_id) if customer_id else None,
                totalAmount=Decimal(str(total_amount)),
                status=OrderStatus.PENDING,
            )
            session.add(order)
            session.commit()
            session.refresh(order)
            result = row_to_dict(order)
        self._emit_status_changed(
            result["id"],
            None,
            OrderStatus.PENDING.value,
            result["product_id"],
            float(result["totalAmount"]),
        )
        return result

    def transition(self, order_id: str, new_status: str) -> dict:
        new = OrderStatus(new_status)
        with SessionLocal() as session:
            order = session.get(Order, _uuid(order_id))
            if order is None:
                raise ValueError(f"订单 {order_id} 未找到")
            allowed = self.VALID_TRANSITIONS[order.status]
            if new not in allowed:
                raise ValueError(f"订单状态不可从 {order.status.value} 变更为 {new.value}")
            old_value = order.status.value
            order.status = new
            session.commit()
            session.refresh(order)
            result = row_to_dict(order)
        self._emit_status_changed(
            order_id,
            old_value,
            new.value,
            result["product_id"],
            float(result["totalAmount"]),
        )
        return result

    def list_by_status(self, status: str) -> list[dict]:
        with SessionLocal() as session:
            orders = session.query(Order).where(Order.status == OrderStatus(status)).all()
            results = []
            for order in orders:
                row = row_to_dict(order)
                row["product"] = row_to_dict(order.product) if order.product else None
                results.append(row)
            return results

    async def execute(self, params: dict[str, Any]) -> Any:
        action = params["action"]
        if action == "create":
            return self.create(params["productId"], float(params["totalAmount"]), params.get("customerId"))
        if action == "transition":
            return self.transition(params["orderId"], params["newStatus"])
        if action == "listByStatus":
            return self.list_by_status(params["status"])
        raise ValueError(f"未知 action: {action}")


class InventoryAlertTool:
    definition = ToolDefinition(
        name="check_inventory",
        description="检查商品库存水平,低于安全线时触发告警",
        parameters=[
            ToolParameter("productName", "string", "商品名称", required=True),
            ToolParameter("currentStock", "number", "当前库存量", required=True),
            ToolParameter("threshold", "number", "安全库存阈值", required=True),
        ],
    )

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus

    def check(self, product_name: str, current_stock: float, threshold: float) -> dict[str, Any]:
        ratio = current_stock / threshold
        alert = ratio < 1
        if ratio <= 0:
            message = f"🔴 {product_name} 已售罄!请立即补货。"
        elif ratio < 0.3:
            message = f"🟠 {product_name} 库存严重不足 (当前: {current_stock}, 安全线: {threshold})。建议3天内补货。"
        elif ratio < 0.6:
            message = f"🟡 {product_name} 库存偏低 (当前: {current_stock}, 安全线: {threshold})。建议7天内补货。"
        elif ratio < 1:
            message = f"🔵 {product_name} 库存接近安全线 (当前: {current_stock})。关注销量趋势。"
        else:
            message = f"✅ {product_name} 库存充足 (当前: {current_stock})。"
        return {"alert": alert, "message": message}

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self.check(params["productName"], float(params["currentStock"]), float(params["threshold"]))
        if result["alert"] and self._event_bus is not None:
            self._event_bus.emit(
                AgentEventType.INVENTORY_ALERT,
                {
                    "productName": params["productName"],
                    "currentStock": float(params["currentStock"]),
                    "threshold": float(params["threshold"]),
                    "message": result["message"],
                },
                source="order-management",
            )
        return result


class AnomalyDetectionTool:
    definition = ToolDefinition(
        name="detect_anomalies",
        description="检测订单异常,识别退货、退款、投诉等问题关键词",
        parameters=[
            ToolParameter("orderDescription", "string", "订单描述文本", required=True),
        ],
    )

    def detect(self, order_description: str) -> dict[str, Any]:
        anomaly_keywords = ["退货", "退款", "投诉", "破损", "延迟", "丢失"]
        matched = [k for k in anomaly_keywords if k in order_description]
        return {
            "anomaly": len(matched) > 0,
            "reason": f"订单包含异常关键词: {', '.join(matched)}" if matched else "正常",
        }

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.detect(params["orderDescription"])
