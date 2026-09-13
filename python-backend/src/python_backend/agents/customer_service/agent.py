"""客服 Agent(spec #7):结构化两节点子图,查证优先(B12)。

verify 节点只暴露 faq_search/knowledge_search/order_lookup/product_lookup;未产生查证证据时 draft 节点不可达
(图级边约束,非提示词):无证据 → nudge 节点明确提示后拉回 verify。
draft 节点暴露翻译/草稿/模板/情感/工单工具,产出最终草稿。
issue #51:终稿据查证命中的切块标识标注引用,归一化为上标编号随答案下发(无命中即无引用)。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from python_backend.agents.base import (
    ToolCallingLlmClient,
    assistant_message,
    merge_lists,
    resolve_tool_calls,
)
from python_backend.agents.customer_service.tools import DRAFT_TOOLS, VERIFY_TOOLS
from python_backend.agents.executor import Executor
from python_backend.core.citations import build_citations, retrieval_hits
from python_backend.domain.tools import ToolRegistry

VERIFY_SYSTEM = """你是跨境电商客服的查证助手。买家消息需要先查证再作答:
- 涉及订单问题:调用 order_lookup 查订单真实状态
- 涉及政策/流程问题:调用 faq_search 查知识库
- 涉及具体商品(价格/库存/在售状态)问题:调用 product_lookup 按 SKU 或标题查证;
  标题命中多条候选时列出候选,不得任选其一
- 查证完成前不得输出最终回复;查证证据会交给起草环节。"""

DRAFT_SYSTEM = """你是跨境电商客服的多语言起草助手。基于买家消息与查证证据起草回复:
- 语言与买家消息一致(中/英/日/德/法)
- 先引用查证证据再作答;证据不足时如实说明,不编造
- 依据知识库命中作答时,在该句末尾用方括号标出所依据的切块标识(如 [faq-returns#6]),
  标识取自查证结果里的 id;未依据命中的句子不标,不要凭记忆编标识
- 需要翻译时用 translate,需要标准话术时用 manage_template 查询
- 无法处理时用 escalate_ticket 升级,并如实告知用户
直接输出最终草稿文本。"""


class CustomerState(TypedDict, total=False):
    slice_description: str
    messages: Annotated[list[dict], merge_lists]
    step_count: int
    tool_calls: list[dict]
    collected: Annotated[list[dict], merge_lists]
    evidence: Annotated[list[dict], merge_lists]  # 已执行的查证调用 {tool, params}
    retrieval: Annotated[list[dict], merge_lists]  # 查证命中的切块(#51:引用只建在命中的 id 上)
    evidence_count: int
    answer: str | None
    incomplete: str | None
    citations: list[dict]


def build_customer_agent(
    *, executor: Executor, llm: ToolCallingLlmClient, step_limit: int = 10
) -> tuple[CompiledStateGraph, ToolRegistry]:
    """构建客服结构化子图,返回 (图, 工具注册表)。"""
    registry = ToolRegistry()
    for tool in VERIFY_TOOLS:
        registry.register(tool, authorized_nodes={"verify"})
    for tool in DRAFT_TOOLS:
        registry.register(tool, authorized_nodes={"draft"})

    verify_visible = {t.name for t in registry.visible_for("verify")}
    draft_visible = {t.name for t in registry.visible_for("draft")}

    def _messages(state: CustomerState, system: str) -> list[dict]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": state["slice_description"]},
            *state.get("messages", []),
        ]

    def _over_limit(state: CustomerState) -> bool:
        return state.get("step_count", 0) >= step_limit

    async def verify_node(state: CustomerState) -> dict:
        if _over_limit(state):
            return {"incomplete": f"步数超限({step_limit}):任务未完成,如实终止"}
        result = await llm.complete_with_tools(
            _messages(state, VERIFY_SYSTEM), [t.to_openai() for t in registry.visible_for("verify")]
        )
        step = {"step_count": state.get("step_count", 0) + 1}
        if result.tool_calls:
            return {
                **step,
                "messages": [assistant_message(result)],
                "tool_calls": result.tool_calls,
            }
        # 无调用轮须清空 tool_calls:该键无 reducer,陈旧值会误导 after_verify 条件边
        return {**step, "messages": [assistant_message(result)], "tool_calls": []}

    async def verify_tools(state: CustomerState) -> dict:
        observations, collected, executed = await resolve_tool_calls(
            state.get("tool_calls", []), visible=verify_visible, executor=executor
        )
        evidence = [{"tool": call.action, "params": call.params} for call in executed]
        retrieval = [hit for call in executed for hit in retrieval_hits(call.result)]
        return {
            "messages": observations,
            "collected": collected,
            "evidence": evidence,
            "retrieval": retrieval,
            "evidence_count": state.get("evidence_count", 0) + len(evidence),
        }

    async def nudge(state: CustomerState) -> dict:
        """B12 边约束的诚实反馈:未查证不得出草稿,明确提示后拉回 verify。"""
        return {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "你尚未调用 faq_search、order_lookup 或 product_lookup 完成查证,不能进入草稿环节。请先查证。"
                    ),
                }
            ]
        }

    async def draft_node(state: CustomerState) -> dict:
        if _over_limit(state):
            return {"incomplete": f"步数超限({step_limit}):任务未完成,如实终止"}
        result = await llm.complete_with_tools(
            _messages(state, DRAFT_SYSTEM), [t.to_openai() for t in registry.visible_for("draft")]
        )
        step = {"step_count": state.get("step_count", 0) + 1}
        if result.tool_calls:
            return {
                **step,
                "messages": [assistant_message(result)],
                "tool_calls": result.tool_calls,
            }
        # issue #51:终稿把命中标识归一化为上标编号;无检索命中则引用为空(不硬标)
        answer, citations = build_citations(result.content or "", state.get("retrieval", []))
        return {
            **step,
            "messages": [assistant_message(result)],
            "tool_calls": [],
            "answer": answer,
            "citations": citations,
        }

    async def draft_tools(state: CustomerState) -> dict:
        observations, collected, _executed = await resolve_tool_calls(
            state.get("tool_calls", []), visible=draft_visible, executor=executor
        )
        return {"messages": observations, "collected": collected}

    def after_verify(state: CustomerState) -> str:
        if state.get("incomplete") is not None:
            return "end"
        if state.get("tool_calls"):
            return "verify_tools"
        if state.get("evidence_count", 0) > 0:
            return "draft"
        return "nudge"

    def after_draft(state: CustomerState) -> str:
        if state.get("incomplete") is not None or state.get("answer") is not None:
            return "end"
        if state.get("tool_calls"):
            return "draft_tools"
        return "end"

    builder = StateGraph(CustomerState)  # ty: ignore
    builder.add_node("verify", verify_node)
    builder.add_node("verify_tools", verify_tools)
    builder.add_node("nudge", nudge)
    builder.add_node("draft", draft_node)
    builder.add_node("draft_tools", draft_tools)
    builder.add_edge(START, "verify")
    builder.add_conditional_edges(
        "verify", after_verify, {"verify_tools": "verify_tools", "draft": "draft", "nudge": "nudge", "end": END}
    )
    builder.add_edge("verify_tools", "verify")
    builder.add_edge("nudge", "verify")
    builder.add_conditional_edges("draft", after_draft, {"draft_tools": "draft_tools", "end": END})
    builder.add_edge("draft_tools", "draft")
    return builder.compile(name="customer_service"), registry
