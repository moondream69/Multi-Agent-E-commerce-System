"""客服 Agent 的 4 个工具(镜像 src/agents/customer-service/tools/*.tool.ts)。"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from python_backend.db.models import FaqEmbedding
from python_backend.db.session import SessionLocal
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
        return self.translate(params["text"], params["targetLocale"])


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
        return self.search(params["question"])


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
        return self.analyze(params["text"])


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

    TEMPLATES: ClassVar[list[dict[str, Any]]] = [
        {
            "id": "greeting",
            "scenario": "问候",
            "template": "您好!感谢您联系客服团队,我是您的专属客服助手。请问有什么可以帮助您的?",
            "locale": "zh-CN",
            "variables": [],
        },
        {
            "id": "order_status",
            "scenario": "订单查询",
            "template": "您的订单 #{order_id} 当前状态为: {order_status}。预计{delivery_date}送达。",
            "locale": "zh-CN",
            "variables": ["order_id", "order_status", "delivery_date"],
        },
        {
            "id": "return_policy",
            "scenario": "退换货",
            "template": "我们支持30天无理由退换货。请确保商品完好,申请后3个工作日内处理。",
            "locale": "zh-CN",
            "variables": [],
        },
        {
            "id": "escalation",
            "scenario": "升级工单",
            "template": "您的问题已转接至高级客服专员,将在24小时内通过邮件与您联系。",
            "locale": "zh-CN",
            "variables": [],
        },
    ]

    def find_template(self, scenario: str, locale: str = "zh-CN") -> dict[str, Any] | None:
        return next((t for t in self.TEMPLATES if t["scenario"] == scenario and t["locale"] == locale), None)

    def fill_template(self, template: dict[str, Any], variables: dict[str, str]) -> str:
        result = template["template"]
        for key, value in variables.items():
            result = result.replace("{" + key + "}", value)
        return result

    def add_template(self, template: dict[str, Any]) -> None:
        self.TEMPLATES.append(template)

    async def execute(self, params: dict[str, Any]) -> Any:
        action = params["action"]
        if action == "find":
            return self.find_template(params.get("scenario") or "", params.get("locale") or "zh-CN")
        if action == "fill":
            tmpl = self.find_template(params.get("scenario") or "", params.get("locale") or "zh-CN")
            if tmpl is None:
                raise ValueError("模板未找到")
            return self.fill_template(tmpl, params.get("variables") or {})
        if action == "add":
            self.add_template(params["template"])
            return {"success": True}
        raise ValueError(f"未知 action: {action}")
