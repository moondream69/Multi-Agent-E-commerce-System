"""客服 Agent 的工具(镜像 src/agents/customer-service/tools/*.tool.ts)。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, ClassVar

from sqlalchemy import select

from python_backend.core.event_bus import EventBus
from python_backend.db.models import FaqEmbedding, Order, ReplyTemplate
from python_backend.db.session import SessionLocal
from python_backend.domain.events import AgentEventType
from python_backend.domain.tasks import ToolDefinition, ToolParameter
from python_backend.infrastructure.embedding import EmbeddingService
from python_backend.infrastructure.llm import LlmService

logger = logging.getLogger(__name__)


class TranslatorTool:
    definition = ToolDefinition(
        name="translate",
        description="将文本翻译到目标语言,支持多语种",
        parameters=[
            ToolParameter("text", "string", "待翻译文本", required=True),
            ToolParameter("targetLocale", "string", "目标语言代码,如 en, es, fr, de, ja, ko", required=True),
        ],
    )

    LOCALE_NAMES: ClassVar[dict[str, str]] = {
        "en": "英语",
        "es": "西班牙语",
        "fr": "法语",
        "de": "德语",
        "ja": "日语",
        "ko": "韩语",
    }

    def __init__(self, llm: LlmService) -> None:
        self._llm = llm

    def translate(self, text: str, target_locale: str) -> str:
        if target_locale == "zh-CN":
            return text
        system = f"你是一个专业的{self.LOCALE_NAMES.get(target_locale, target_locale)}翻译。请准确翻译,保持语气自然。"
        return self._llm.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"翻译为{target_locale}: {text}"},
            ],
            temperature=0.3,
            max_tokens=500,
        )

    async def execute(self, params: dict[str, Any]) -> str:
        # LLM 同步调用卸载出事件循环(与 ReAct 图 agent_node 一致)
        return await asyncio.to_thread(self.translate, params["text"], params["targetLocale"])


class FaqRetrievalTool:
    definition = ToolDefinition(
        name="faq_search",
        description="在FAQ知识库中检索与用户问题最匹配的答案",
        parameters=[
            ToolParameter("question", "string", "用户问题", required=True),
            ToolParameter("locale", "string", "语言代码,默认 zh-CN", required=False),
        ],
    )

    def __init__(self) -> None:
        self._embedding = EmbeddingService()

    def search(self, question: str) -> str:
        with SessionLocal() as session:
            results = self._embedding.search(session, FaqEmbedding, question, top_k=3, threshold=0.5)
        if not results:
            return "未找到相关FAQ条目,建议转人工客服处理。"
        return "\n\n".join(f"{i + 1}. Q: {r['question']}\nA: {r['answer']}" for i, r in enumerate(results))

    async def execute(self, params: dict[str, Any]) -> str:
        # Embedding 检索(HTTP + DB)同步段卸载出事件循环
        return await asyncio.to_thread(self.search, params["question"])


class SentimentAnalysisTool:
    definition = ToolDefinition(
        name="sentiment_analysis",
        description="分析用户文本的情感倾向,返回正/中/负及置信度",
        parameters=[
            ToolParameter("text", "string", "待分析的用户文本", required=True),
        ],
    )

    def __init__(self, llm: LlmService) -> None:
        self._llm = llm

    def analyze(self, text: str) -> dict[str, Any]:
        response = self._llm.complete(
            [
                {
                    "role": "system",
                    "content": '分析以下文本的情感,返回 JSON: { "sentiment": "positive|neutral|negative", "score": 0-1, "keywords": [] }',  # noqa: E501
                },
                {"role": "user", "content": text},
            ],
            temperature=0,
            max_tokens=200,
            json_mode=True,
        )
        try:
            return json.loads(response)
        except Exception:
            return {"sentiment": "neutral", "score": 0.5, "keywords": []}

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.analyze, params["text"])


class TemplateManagerTool:
    definition = ToolDefinition(
        name="manage_template",
        description="管理客服回复话术模板,支持查找、填充变量和新增模板",
        parameters=[
            ToolParameter("action", "string", "操作类型: find|fill|add", required=True),
            ToolParameter("scenario", "string", "场景名称 (find 时使用)", required=False),
            ToolParameter("locale", "string", "语言代码 (find 时使用)", required=False),
            ToolParameter("templateId", "string", "模板 ID (fill 时使用,与 scenario 二选一)", required=False),
            ToolParameter("variables", "object", "模板变量键值对 (fill 时使用)", required=False),
            ToolParameter(
                "template",
                "object",
                "新模板对象,需包含 id, scenario, template, locale, variables 字段 (add 时使用)",
                required=False,
            ),
        ],
    )

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus

    @staticmethod
    def _to_contract(row: ReplyTemplate) -> dict[str, Any]:
        """ORM 行 → 工具契约形状(5 键,与 LLM 定义一致,不含 createdAt/updatedAt)。"""
        return {
            "id": row.id,
            "scenario": row.scenario,
            "template": row.template,
            "locale": row.locale,
            "variables": list(row.variables or []),
        }

    def find_template(self, scenario: str, locale: str = "zh-CN") -> dict[str, Any] | None:
        with SessionLocal() as session:
            row = session.scalar(
                select(ReplyTemplate).where(
                    ReplyTemplate.scenario == scenario,
                    ReplyTemplate.locale == locale,
                )
            )
            return self._to_contract(row) if row else None

    def fill_template(self, template: dict[str, Any], variables: dict[str, str]) -> str:
        result = template["template"]
        for key, value in variables.items():
            result = result.replace("{" + key + "}", value)
        return result

    def add_template(self, template: dict[str, Any]) -> bool:
        """持久化新增:自然键 (scenario, locale) 已存在则跳过(幂等,与 seed 约定一致)。"""
        locale = template.get("locale") or "zh-CN"
        with SessionLocal() as session:
            exists = session.scalar(
                select(ReplyTemplate.id).where(
                    ReplyTemplate.scenario == template["scenario"],
                    ReplyTemplate.locale == locale,
                )
            )
            if exists:
                return False
            session.add(
                ReplyTemplate(
                    id=template["id"],
                    scenario=template["scenario"],
                    template=template["template"],
                    locale=locale,
                    variables=template.get("variables") or [],
                )
            )
            session.commit()
            return True

    async def execute(self, params: dict[str, Any]) -> Any:
        action = params["action"]
        if action == "find":
            return self.find_template(params.get("scenario") or "", params.get("locale") or "zh-CN")
        if action == "fill":
            tmpl = self.find_template(params.get("scenario") or "", params.get("locale") or "zh-CN")
            if tmpl is None:
                raise ValueError("模板未找到")
            result = self.fill_template(tmpl, params.get("variables") or {})
            if self._event_bus is not None:
                self._event_bus.emit(
                    AgentEventType.REPLY_GENERATED,
                    {"scenario": tmpl["scenario"], "templateId": tmpl["id"]},
                    source="customer-service",
                )
            return result
        if action == "add":
            self.add_template(params["template"])
            return {"success": True}
        raise ValueError(f"未知 action: {action}")


class OrderLookupTool:
    definition = ToolDefinition(
        name="order_lookup",
        description="查询订单当前状态与商品信息,用于售后沟通",
        parameters=[
            ToolParameter("orderId", "string", "订单 ID", required=True),
        ],
    )

    def lookup(self, order_id: str) -> dict[str, Any]:
        with SessionLocal() as session:
            order = session.get(Order, uuid.UUID(order_id))
            if order is None:
                raise ValueError(f"订单 {order_id} 未找到")
            return {
                "orderId": str(order.id),
                "status": order.status.value,
                "productTitle": order.product.title if order.product else None,
                "totalAmount": float(order.totalAmount),
                "currency": order.currency,
                "updatedAt": order.updatedAt.isoformat(),
            }

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.lookup(params["orderId"])


class EscalateTicketTool:
    definition = ToolDefinition(
        name="escalate_ticket",
        description="将客户问题升级到人工客服处理",
        parameters=[
            ToolParameter("orderId", "string", "关联订单 ID (可选)", required=False),
            ToolParameter("reason", "string", "升级原因", required=True),
        ],
    )

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._event_bus is not None:
            self._event_bus.emit(
                AgentEventType.ESCALATION_TRIGGERED,
                {"orderId": params.get("orderId"), "reason": params["reason"]},
                source="customer-service",
            )
        return {"success": True, "message": "已将问题升级至人工客服,我们会尽快处理。"}
