"""客服 Agent(spec #7):结构化两节点子图,查证优先(B12)。

verify 节点只暴露 faq_search/knowledge_search/order_lookup/product_lookup;未产生查证证据时 draft 节点不可达
(图级边约束,非提示词):无证据 → nudge 节点明确提示后拉回 verify。
draft 节点暴露翻译/草稿/模板/情感/工单工具,产出最终草稿。
issue #51:终稿据查证命中的切块标识标注引用,归一化为上标编号随答案下发(无命中即无引用)。
issue #69:verify 的**查回结果**(订单/商品)随状态出块,经 make_agent_runner 落进切片证据块
——任务线的商品/订单类结论同样要在判分材料里可核(与工作台线 #67 同一缺口)。
issue #71:起草提示词补通用方括号约束(方括号只作引用标记;#57 机械线对未解析的方括号 token
一律判疑似伪造,`[done]` 类自造标记在选品线已被判过一次)。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from python_backend.agents.base import (
    ToolCallingLlmClient,
    answer_turn,
    assistant_message,
    evidence_calls_from,
    merge_lists,
    resolve_tool_calls,
    retrieval_hits_from,
    slice_prompt,
)
from python_backend.agents.customer_service.tools import DRAFT_TOOLS, VERIFY_TOOLS
from python_backend.agents.executor import Executor
from python_backend.domain.tools import ToolRegistry
from python_backend.infrastructure.llm import AGENT_MAX_TOKENS

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
- 方括号只用于引用标记:不要用方括号写状态、备注或强调(如 [done]);未解析的方括号会被判成疑似伪造编号
- 需要翻译时用 translate,需要标准话术时用 manage_template 查询
- 无法处理时用 escalate_ticket 升级,并如实告知用户
直接输出最终草稿文本。"""


class CustomerState(TypedDict, total=False):
    slice_description: str
    task_request: str  # 原始用户请求(#64 B2:与 ReAct 线同一口径,见 base.slice_prompt)
    messages: Annotated[list[dict], merge_lists]
    step_count: int
    blank_retried: bool  # 终稿空正文已重试过一次(#64 B1:只重试一次即止)
    tool_calls: list[dict]
    collected: Annotated[list[dict], merge_lists]
    verify_calls: Annotated[list[dict], merge_lists]  # 已执行的查证调用 {tool, params}(B12 的判定面)
    evidence_calls: Annotated[list[dict], merge_lists]  # #69:系统记录类查证结果(出块/裁剪在 runner)
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
            {"role": "user", "content": slice_prompt(state.get("task_request", ""), state["slice_description"])},
            *state.get("messages", []),
        ]

    def _over_limit(state: CustomerState) -> bool:
        return state.get("step_count", 0) >= step_limit

    async def verify_node(state: CustomerState) -> dict:
        if _over_limit(state):
            return {"incomplete": f"步数超限({step_limit}):任务未完成,如实终止"}
        result = await llm.complete_with_tools(
            _messages(state, VERIFY_SYSTEM),
            [t.to_openai() for t in registry.visible_for("verify")],
            max_tokens=AGENT_MAX_TOKENS,
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
        verify_calls = [{"tool": call.action, "params": call.params} for call in executed]
        retrieval = retrieval_hits_from(executed)
        return {
            "messages": observations,
            "collected": collected,
            "verify_calls": verify_calls,
            # #69:查回的商品/订单结果另收一份(判分材料据此核商品类结论)
            "evidence_calls": evidence_calls_from(executed),
            "retrieval": retrieval,
            "evidence_count": state.get("evidence_count", 0) + len(verify_calls),
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
            _messages(state, DRAFT_SYSTEM),
            [t.to_openai() for t in registry.visible_for("draft")],
            max_tokens=AGENT_MAX_TOKENS,
        )
        step = {"step_count": state.get("step_count", 0) + 1}
        if result.tool_calls:
            return {
                **step,
                "messages": [assistant_message(result)],
                "tool_calls": result.tool_calls,
            }
        # issue #51:终稿把命中标识归一化为上标编号;无检索命中则引用为空(不硬标)
        # #64 B1:终稿是客服线的「作答轮」,空正文走与 ReAct 线同一个出口(重试一次 → 未完成)
        return answer_turn(state, result, step)

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
        # 到此只剩一种情形:终稿空正文的**重试轮**(#64 B1,节点已把提示词追加进消息史)——
        # 回 draft 再问一次;仍空时节点会给出 incomplete,不走这里。
        return "draft"

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
    builder.add_conditional_edges("draft", after_draft, {"draft_tools": "draft_tools", "draft": "draft", "end": END})
    builder.add_edge("draft_tools", "draft")
    return builder.compile(name="customer_service"), registry
