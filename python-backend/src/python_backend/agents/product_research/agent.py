"""选品分析 Agent(spec #7):ReAct 子图,情报 → 评分 → 报告 → 自动建草稿。"""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

from python_backend.agents.base import ToolCallingLlmClient, build_react_agent
from python_backend.agents.executor import Executor
from python_backend.agents.product_research.tools import PRODUCT_TOOLS
from python_backend.domain.tools import ToolRegistry

SYSTEM_PROMPT = """你是选品分析 Agent,服务于跨境电商卖家。工作流程:
1. 用 trend_query / competitor_analysis 检索市场情报(优先检索,不要凭空作答)
2. 用 scoring 对候选商品打分
3. 用 generate_report 生成结构化分析报告(含评分等级)
4. 报告完成后,用 draft_create 把结论落为商品草稿(免审直行),并告知用户草稿已建

只输出事实性结论,情报不足时如实说明。"""


def build_product_agent(
    *, executor: Executor, llm: ToolCallingLlmClient, step_limit: int = 10
) -> tuple[CompiledStateGraph, ToolRegistry]:
    """构建选品子图,返回 (图, 工具注册表)——注册表供结构测试与装配检查。"""
    registry = ToolRegistry()
    for tool in PRODUCT_TOOLS:
        registry.register(tool, authorized_nodes={"agent"})
    graph = build_react_agent(
        name="product_research",
        system_prompt=SYSTEM_PROMPT,
        registry=registry,
        executor=executor,
        llm=llm,
        step_limit=step_limit,
    )
    return graph, registry
