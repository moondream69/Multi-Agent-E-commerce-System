"""意图解析:确定性关键词规则(镜像 intent-parser.service.ts,含兜底)。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from python_backend.domain.tasks import TaskType


@dataclass
class ParseResult:
    task_type: TaskType
    extracted_input: dict[str, Any]


class IntentParser:
    _PATTERNS: list[tuple[TaskType, str, list[str]]] = [
        (
            TaskType.PRODUCT_RESEARCH,
            "analyze",
            ["选品", "市场", "趋势", "竞品", "分析报告", "什么产品好卖"],
        ),
        (
            TaskType.ORDER_MANAGEMENT,
            "create_product",
            ["订单", "商品", "上架", "库存", "发货", "物流"],
        ),
        (
            TaskType.CUSTOMER_SERVICE,
            "handle_query",
            ["客户", "投诉", "FAQ", "翻译", "回复", "售后", "退货"],
        ),
    ]

    def parse(self, text: str) -> ParseResult:
        """命中关键词越多者优先;平局保持配置顺序(NestJS 版为纯顺序命中,曾把
        "客户抱怨物流太慢帮我写回复" 误路由到订单 Agent——"物流"压制了"客户/回复")。"""
        best: tuple[int, TaskType, str] | None = None
        for task_type, action, keywords in self._PATTERNS:
            hits = sum(1 for kw in keywords if kw in text)
            if hits > 0 and (best is None or hits > best[0]):
                best = (hits, task_type, action)
        if best is not None:
            return ParseResult(task_type=best[1], extracted_input={"action": best[2], "query": text})
        return ParseResult(
            task_type=TaskType.CUSTOMER_SERVICE,
            extracted_input={"action": "handle_query", "text": text},
        )
