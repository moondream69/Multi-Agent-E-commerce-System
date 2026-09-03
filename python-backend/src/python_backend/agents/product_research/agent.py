"""选品分析 Agent(镜像 src/agents/product-research/product-research.agent.ts)。"""

from __future__ import annotations

import logging

from python_backend.core.base_agent import BaseAgent
from python_backend.core.event_bus import EventBus
from python_backend.domain.events import AgentEvent
from python_backend.infrastructure.llm import LlmService

from .tools import CompetitorAnalysisTool, ReportGeneratorTool, ScoringTool, TrendQueryTool

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是跨境电商选品分析专家。你的任务是根据用户需求进行选品分析。

## 可用工具
- trend_query: 查询品类市场趋势数据,参数 category(品类名称), period(时间范围,如 last_30_days)
- competitor_analysis: 竞品对比分析,参数 category(品类名称), keywords(关键词数组)
- scoring: 选品评分,参数 searchVolume(搜索量), competition(竞争度0-100), avgPrice(均价), margin(利润率%), growthRate(增长率%)
- generate_report: 生成最终的选品分析报告,参数 title(报告标题), sections(章节数组,每项含title和content)

## 工作流程
1. 先调用 trend_query 了解市场趋势
2. 再调用 competitor_analysis 分析竞品
3. 然后调用 scoring 进行选品评分
4. 最后调用 generate_report 生成完整报告

## 规则
- 如果某个工具返回"未找到数据",继续执行后续步骤,不要中断
- 评分时如果用户没提供具体数字,使用合理的估算值
- 最终必须调用 generate_report 来生成报告
- 报告生成后,你的工作就完成了,不需要再做其他事情"""


class ProductResearchAgent(BaseAgent):
    id = "product-research"
    name = "选品分析Agent"
    description = "负责跨境电商选品分析:趋势洞察、竞品对比、选品评分和报告生成"
    system_prompt = SYSTEM_PROMPT

    def __init__(
        self,
        event_bus: EventBus,
        llm: LlmService,
        trend_query: TrendQueryTool,
        competitor_analysis: CompetitorAnalysisTool,
        scoring: ScoringTool,
        report_generator: ReportGeneratorTool,
    ) -> None:
        super().__init__(event_bus, llm)
        self.tools = [trend_query, competitor_analysis, scoring, report_generator]

    async def handle_event(self, event: AgentEvent) -> None:
        logger.info("收到事件: %s from %s", event.type, event.source)
