"""订单处理 Agent(镜像 src/agents/order-management/order-management.agent.ts)。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from python_backend.core.base_agent import BaseAgent
from python_backend.core.event_bus import EventBus
from python_backend.domain.events import AgentEvent, AgentEventType
from python_backend.infrastructure.llm import LlmService

from .tools import (
    AnomalyDetectionTool,
    InventoryAlertTool,
    OrderWorkflowTool,
    ProductCrudTool,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是跨境电商订单处理助手。根据用户需求执行商品管理、订单处理和库存检测。

## 可用工具
- product_crud: 商品管理,参数 action(create/listByCategory/findBySku/updateStatus) + 对应字段
- order_workflow: 订单管理,参数 action(create/transition/listByStatus) + 对应字段
- check_inventory: 库存预警检查,参数 productName(商品名), currentStock(当前库存), threshold(安全线)
- detect_anomalies: 异常订单检测,参数 orderDescription(订单描述文本)

## 规则
- 创建商品成功后告知用户商品ID和SKU
- 更新订单状态时务必验证状态转换是否合法
- 库存不足时给出明确的补货建议
- 检测到异常订单时说明异常原因"""


class OrderManagementAgent(BaseAgent):
    id = "order-management"
    name = "订单处理Agent"
    description = "负责商品管理、订单生命周期、库存预警和异常检测"
    system_prompt = SYSTEM_PROMPT

    def __init__(
        self,
        event_bus: EventBus,
        llm: LlmService,
        product_crud: ProductCrudTool,
        order_workflow: OrderWorkflowTool,
        inventory_alert: InventoryAlertTool,
        anomaly_detection: AnomalyDetectionTool,
    ) -> None:
        super().__init__(event_bus, llm)
        self.tools = [product_crud, order_workflow, inventory_alert, anomaly_detection]
        self._product_crud = product_crud

    async def handle_event(self, event: AgentEvent) -> None:
        if event.type == AgentEventType.REPORT_GENERATED:
            await self._create_draft_from_report(event.payload or {})

    async def _create_draft_from_report(self, payload: dict[str, Any]) -> None:
        """选品报告完成 → LLM 提炼商品信息 → product_crud 创建草稿;提炼失败降级为报告标题 + 默认价。"""
        try:
            product = await asyncio.to_thread(self._extract_product, payload)
            created = self._product_crud.create(
                product["sku"],
                product["title"],
                product["price"],
                product["category"],
                product.get("description"),
            )
            logger.info("由选品报告生成商品草稿: %s (%s)", created["id"], created["title"])
        except Exception as error:
            logger.warning("自动生成商品草稿失败: %s", error)

    def _extract_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        """同步 LLM 提炼(调用方用 to_thread 卸载);失败降级不抛异常。"""
        title = payload.get("title") or "自动导入商品"
        try:
            raw = self._llm.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "从选品报告中提炼一个可上架的商品,返回 JSON:"
                            '{"sku": string, "title": string, "price": number,'
                            ' "category": string, "description": string}'
                        ),
                    },
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)[:3000]},
                ],
                temperature=0,
                max_tokens=300,
                json_mode=True,
            )
            data = json.loads(raw)
            return {
                "sku": data["sku"],
                "title": data["title"],
                "price": float(data["price"]),
                "category": data.get("category") or "自动导入",
                "description": data.get("description"),
            }
        except Exception:
            return {
                "sku": f"auto-{uuid.uuid4().hex[:8]}",
                "title": title,
                "price": 59.9,
                "category": "自动导入",
                "description": None,
            }
