"""客服 Agent(镜像 src/agents/customer-service/customer-service.agent.ts)。"""

from __future__ import annotations

import logging
from typing import Any

from python_backend.core.base_agent import BaseAgent
from python_backend.core.event_bus import EventBus
from python_backend.core.workflow import Workflow, WorkflowPhase
from python_backend.domain.events import AgentEvent, AgentEventType
from python_backend.infrastructure.llm import LlmService

from .tools import (
    EscalateTicketTool,
    FaqRetrievalTool,
    OrderLookupTool,
    SentimentAnalysisTool,
    TemplateManagerTool,
    TranslatorTool,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是跨境电商智能客服。为客户提供多语言支持、FAQ检索和情感分析。

## 可用工具
- translate: 多语言翻译,参数 text(原文), targetLocale(目标语言如en/es/fr/de/ja/ko)
- faq_search: FAQ检索,参数 question(问题文本), locale(语言,默认zh-CN)
- sentiment_analysis: 情感分析,参数 text(待分析文本),返回 sentiment(positive/neutral/negative)和score(0-1)
- manage_template: 话术模板管理,参数 action(find/fill/add) + 对应字段
- order_lookup: 订单查询,参数 orderId(订单ID),售后场景查询订单状态
- escalate_ticket: 问题升级,参数 orderId(可选), reason(升级原因)

## 工作流程
1. 首先理解客户的问题
2. 调用 sentiment_analysis 分析客户情绪
3. 调用 faq_search 查找相关答案
4. 如果找到FAQ就用 manage_template 生成回复
5. 如果需要翻译就调用 translate
6. 如果客户情绪非常负面(sentiment=negative且score>0.8),明确告知需要升级

## 规则
- 始终以友好、专业的态度回复
- 优先使用FAQ已有答案,不要编造信息
- 翻译时保持原文语气
- 遇到无法解决的问题,建议升级到人工客服
- 情感为负且FAQ无法解决时,调用 escalate_ticket 升级处理
- 客户询问订单状态时,先调用 order_lookup 查询真实状态再回复"""


# 图级工作流声明:阶段1 必调 sentiment_analysis + faq_search(顺序不限),完成前禁止直接回答;
# 阶段2 解锁全部工具并可自由回答。manage_template/translate 为条件性,不强制(交 LLM 判断)。
CUSTOMER_SERVICE_WORKFLOW = Workflow(
    phases=(
        WorkflowPhase(
            required_tools=frozenset({"sentiment_analysis", "faq_search"}),
            allowed_tools=frozenset({"sentiment_analysis", "faq_search"}),
            can_answer=False,
        ),
        WorkflowPhase(required_tools=frozenset(), allowed_tools=None, can_answer=True),
    )
)

# 订单状态 → 买家通知文案(订单号占位)
_ORDER_NOTIFICATIONS: dict[str, str] = {
    "pending": "您的订单 #{order_id} 已提交,等待商家确认。",
    "confirmed": "您的订单 #{order_id} 已确认,准备处理。",
    "processing": "您的订单 #{order_id} 正在处理中,请耐心等待。",
    "shipped": "您的订单 #{order_id} 已发货,请留意物流信息。",
    "delivered": "您的订单 #{order_id} 已送达,感谢您的购买!",
    "cancelled": "您的订单 #{order_id} 已取消。如有疑问请随时联系客服。",
    "returned": "您的订单 #{order_id} 已完成退货处理。",
}


class CustomerServiceAgent(BaseAgent):
    id = "customer-service"
    name = "客服Agent"
    description = "多语言客服,FAQ 检索,情感分析,话术生成,异常升级"
    system_prompt = SYSTEM_PROMPT
    workflow = CUSTOMER_SERVICE_WORKFLOW

    def __init__(
        self,
        event_bus: EventBus,
        llm: LlmService,
        translator: TranslatorTool,
        faq_retrieval: FaqRetrievalTool,
        sentiment_analysis: SentimentAnalysisTool,
        template_manager: TemplateManagerTool,
        order_lookup: OrderLookupTool,
        escalate_ticket: EscalateTicketTool,
    ) -> None:
        super().__init__(event_bus, llm)
        self.tools = [translator, faq_retrieval, sentiment_analysis, template_manager, order_lookup, escalate_ticket]

    async def handle_event(self, event: AgentEvent) -> None:
        if event.type == AgentEventType.ORDER_STATUS_CHANGED:
            self._notify_order_status(event.payload or {})
        elif event.type == AgentEventType.INVENTORY_ALERT:
            self._notify_inventory_alert(event.payload or {})

    def _notify_order_status(self, payload: dict[str, Any]) -> None:
        to = payload.get("to")
        order_id = payload.get("orderId")
        template = _ORDER_NOTIFICATIONS.get(to or "")
        if template is None or order_id is None:
            return
        self._event_bus.emit(
            AgentEventType.CUSTOMER_NOTIFICATION,
            {
                "message": template.format(order_id=order_id),
                "agentId": self.id,
                "orderId": order_id,
            },
            source=self.id,
        )

    def _notify_inventory_alert(self, payload: dict[str, Any]) -> None:
        self._event_bus.emit(
            AgentEventType.CUSTOMER_NOTIFICATION,
            {
                "message": payload.get("message") or "库存告警",
                "agentId": self.id,
            },
            source=self.id,
        )
