"""选品分析 Agent 的 4 个工具(镜像 src/agents/product-research/tools/*.tool.ts)。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from python_backend.db.models import MarketEmbedding, ProductEmbedding
from python_backend.db.session import SessionLocal
from python_backend.domain.tasks import ToolDefinition, ToolParameter
from python_backend.infrastructure.embedding import EmbeddingService

logger = logging.getLogger(__name__)


class TrendQueryTool:
    definition = ToolDefinition(
        name="trend_query",
        description="查询品类市场趋势数据,了解搜索量变化和热度趋势",
        parameters=[
            ToolParameter("category", "string", "品类名称", required=True),
            ToolParameter("period", "string", "时间范围,如 last_30_days", required=False),
        ],
    )

    def __init__(self) -> None:
        self._embedding = EmbeddingService()

    def query(self, category: str, period: str) -> str:
        with SessionLocal() as session:
            results = self._embedding.search(
                session, MarketEmbedding, f"{category} 市场趋势 {period}", top_k=5, threshold=0.5
            )
        if not results:
            return f"未找到 {category} 在 {period} 的趋势数据。建议扩大搜索范围。"
        summary = "\n".join(r["content"] for r in results)
        return f"## {category} 趋势分析 ({period})\n\n{summary}"

    async def execute(self, params: dict[str, Any]) -> str:
        return self.query(params["category"], params.get("period") or "last_30_days")


class CompetitorAnalysisTool:
    definition = ToolDefinition(
        name="competitor_analysis",
        description="分析竞品数据,对比价格、评分和市场份额",
        parameters=[
            ToolParameter("category", "string", "品类名称", required=True),
            ToolParameter("keywords", "array", "搜索关键词列表", required=True),
        ],
    )

    def __init__(self) -> None:
        self._embedding = EmbeddingService()

    def analyze(self, category: str, keywords: list[str]) -> str:
        query = f"{category} {' '.join(keywords)} 竞品对比 价格 评分"
        with SessionLocal() as session:
            results = self._embedding.search(session, ProductEmbedding, query, top_k=10, threshold=0.4)
        if not results:
            return f"未找到 {category} 相关竞品数据。"
        lines = [
            f"{i + 1}. {r['content']} (相似度: {(r['score'] * 100):.1f}%)"
            for i, r in enumerate(results)
        ]
        return f"## {category} 竞品分析\n\n找到 {len(results)} 个相关竞品:\n\n" + "\n".join(lines)

    async def execute(self, params: dict[str, Any]) -> str:
        return self.analyze(params["category"], params["keywords"])


class ScoringTool:
    definition = ToolDefinition(
        name="scoring",
        description="对选品进行多维度评分,输出综合得分和等级",
        parameters=[
            ToolParameter("searchVolume", "number", "搜索量", required=True),
            ToolParameter("competition", "number", "竞争度 (0-100)", required=True),
            ToolParameter("avgPrice", "number", "平均价格", required=True),
            ToolParameter("margin", "number", "利润率 (0-100)", required=True),
            ToolParameter("growthRate", "number", "市场增长率", required=True),
        ],
    )

    def calculate(self, input_: dict[str, float]) -> dict[str, Any]:
        search_score = min(input_["searchVolume"] / 1000, 100) * 0.25
        competition_score = (100 - input_["competition"]) * 0.25
        price_score = min(input_["avgPrice"] / 100, 100) * 0.2
        margin_score = input_["margin"] * 0.2
        growth_score = max(0, input_["growthRate"] + 50) * 0.1

        score = search_score + competition_score + price_score + margin_score + growth_score
        if score >= 80:
            grade = "A (强烈推荐)"
        elif score >= 60:
            grade = "B (推荐)"
        elif score >= 40:
            grade = "C (谨慎)"
        else:
            grade = "D (不推荐)"
        return {
            "score": round(score * 10) / 10,
            "grade": grade,
            "breakdown": {
                "searchScore": search_score,
                "competitionScore": competition_score,
                "priceScore": price_score,
                "marginScore": margin_score,
                "growthScore": growth_score,
            },
        }

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.calculate(
            {
                "searchVolume": float(params["searchVolume"]),
                "competition": float(params["competition"]),
                "avgPrice": float(params["avgPrice"]),
                "margin": float(params["margin"]),
                "growthRate": float(params["growthRate"]),
            }
        )


class ReportGeneratorTool:
    definition = ToolDefinition(
        name="generate_report",
        description="生成格式化的选品分析报告",
        parameters=[
            ToolParameter("title", "string", "报告标题", required=True),
            ToolParameter("sections", "array", "报告章节列表,每项包含 title 和 content", required=True),
        ],
    )

    def generate(self, title: str, sections: list[dict[str, str]]) -> str:
        header = (
            f"# 📊 选品分析报告: {title}\n\n"
            f"> 生成时间: {datetime.now(timezone.utc).isoformat()}\n\n---\n\n"
        )
        body = "".join(f"## {s['title']}\n\n{s['content']}\n\n" for s in sections)
        footer = f"---\n\n*本报告由选品分析 Agent 自动生成*"
        return header + body + footer

    async def execute(self, params: dict[str, Any]) -> str:
        return self.generate(params["title"], params["sections"])
