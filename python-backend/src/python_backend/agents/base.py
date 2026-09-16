"""共享 ReAct 子图构造器(spec #7):agent 节点 + tool 节点循环,步数上限 10(B17)。

工具按节点授权暴露(B6):agent 节点只看见本节点授权清单(LLM 看不到未授权工具);
tool 节点按「可见性 → 三层分类」裁决:不可见拒绝(边约束的兜底)/ approval 收集不执行
(诚实观察)/ auto 直行。resolve_tool_calls 为客服结构化图共用。

issue #52:引用增强是横切能力(ADR-0007 C 段)——检索类工具的命中累积进状态,作答轮经
build_citations 归一化(与客服线同一形状/同一解析点);无命中即文本原样、引用为空。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from python_backend.agents.executor import Executor, action_of
from python_backend.core.approvals import classify_action
from python_backend.core.citations import build_citations, retrieval_hits
from python_backend.core.planning import Slice
from python_backend.domain.tools import ToolRegistry
from python_backend.infrastructure.llm import LlmClient, LlmFailure, ToolCallResult


class ToolCallingLlmClient(LlmClient, Protocol):
    """子图专用 LLM 协议:在 LlmClient 之上要求工具调用轮(规划器不需要)。"""

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> ToolCallResult: ...


def merge_lists(current: list, update: list) -> list:
    """列表合并 reducer(Annotated 约定:reducer(current, single_update))。"""
    result = list(current)
    result.extend(update)
    return result


def assistant_message(result: ToolCallResult) -> dict:
    """LLM 轮次结果 → 助手消息(两个子图共用,别各写一份)。

    issue #27:思考模式的 reasoning_content 须在下轮请求里原样回传(空串也要保留该键),
    否则 DeepSeek V4 类思考模型返 400;响应无该键(非思考模式)则不写字段。
    """
    message: dict = {"role": "assistant", "content": result.content or ""}
    if result.reasoning_content is not None:
        message["reasoning_content"] = result.reasoning_content
    if result.tool_calls:
        message["tool_calls"] = result.tool_calls
    return message


class AgentState(TypedDict, total=False):
    """业务子图状态:ReAct 消息史 + 审批动作收集 + 终态(答案/未完成)。"""

    slice_description: str
    task_request: str  # 原始用户请求(#64 B2:切片执行段的全局任务,随 Send 下发)
    messages: Annotated[list[dict], merge_lists]
    step_count: int
    blank_retried: bool  # 作答轮空正文已重试过一次(#64 B1:只重试一次即止)
    tool_calls: list[dict]
    collected: Annotated[list[dict], merge_lists]  # 审批动作参数快照(效果后置,切片边界打包)
    retrieval: Annotated[list[dict], merge_lists]  # 检索命中切块(#52:引用只建在命中的 id 上)
    answer: str | None
    incomplete: str | None
    citations: list[dict]  # issue #51:检索类答案的引用条目(无检索依据即空)


# 作答轮正文为空的两句文案(#64 B1):重试提示进消息史,未完成文案进切片终态(incomplete)。
# 「正文为空」四字是护栏的对外口径(B30③),不要在别处另造说法。
BLANK_ANSWER_RETRY = "你上一轮没有输出任何正文。请直接输出最终答复的正文内容。"
BLANK_ANSWER_INCOMPLETE = "正文为空:作答轮未产出正文(重试后仍为空),任务未完成"


def slice_prompt(task_request: str, description: str) -> str:
    """切片执行段的用户消息:全局任务 + 本切片职责 两段(#64 B2)。

    **两段都要**:原请求缺席时,切片描述一旦没点名对象(规划器写「基于切片1数据做需求趋势与价格带
    分析…(漏水、续航、清洗难等)」),执行段只能从字面猜——2026-09-16 实录里它猜成了「宠物饮水机」,
    整片跑偏。全局任务缺省(直接驱动子图的测试与旧调用)时退化为纯描述,不摆一个空的「全局任务」段。
    """
    if not task_request.strip():
        return description
    return f"【全局任务】{task_request}\n\n【本切片职责】{description}"


def answer_is_blank(result: ToolCallResult) -> bool:
    """作答轮(无工具调用轮)是否为空正文(#64 B1)。

    判据取**内容去空白后为空**,不附加「reasoning 非空」的前提:思考模式下正文整体落进
    ``reasoning_content``(outdoor-trend 实录)只是成因之一,任何空正文都不是答案——而旧口径把
    空串当作答,``answer=""`` 在 ``results`` 里被 falsy 判掉(``graph.py`` 的 ``if run.get("answer")``),
    任务却照报 ``completed``,快照落一个 ``answer: null`` 直到 judge 才暴露。
    """
    return not (result.content or "").strip()


def answer_turn(state: AgentState, result: ToolCallResult, step: dict) -> dict:
    """作答轮收尾的**单一出口**(ReAct 线 agent 节点 + 客服线 draft 节点共用)。

    空正文 → 本切片内**同预算再问一次**(重试轮照常计步,不额外放宽 step_limit);已有一次仍空
    → 判未完成。非空 → 归一化引用后出答案。
    """
    if answer_is_blank(result):
        if state.get("blank_retried"):
            return {
                **step,
                "messages": [assistant_message(result)],
                "tool_calls": [],
                "incomplete": BLANK_ANSWER_INCOMPLETE,
            }
        return {
            **step,
            "blank_retried": True,
            "messages": [assistant_message(result), {"role": "user", "content": BLANK_ANSWER_RETRY}],
            "tool_calls": [],
        }
    # issue #52:作答轮把命中标识归一化为上标编号(与客服线同一解析点);无检索命中则文本原样、引用为空
    answer, citations = build_citations(result.content or "", state.get("retrieval", []))
    # 作答轮须清空 tool_calls:该键无 reducer,上一轮的陈旧值会误导条件边
    return {
        **step,
        "messages": [assistant_message(result)],
        "tool_calls": [],
        "answer": answer,
        "citations": citations,
    }


@dataclass(frozen=True)
class ExecutedCall:
    """一次已执行的免审工具调用:结果供证据记录与引用解析(issue #51)。"""

    action: str
    params: dict
    result: object


def retrieval_hits_from(executed: list[ExecutedCall]) -> list[dict]:
    """已执行调用里的检索命中汇总(按调用序拼接,不去重)——引用只建在命中的 id 上。

    两个子图共用(issue #51 客服线 / #52 ReAct 线):非检索工具的结果没有 hits 形状,自然落空。
    """
    return [hit for call in executed for hit in retrieval_hits(call.result)]


async def resolve_tool_calls(
    tool_calls: list[dict], *, visible: set[str], executor: Executor
) -> tuple[list[dict], list[dict], list[ExecutedCall]]:
    """执行/收集一轮工具调用 → (观察消息, 收集的审批动作, 已执行的 auto 动作)。

    裁决顺序(B6/B7):可见性 → 三层分类;一切失败转诚实观察,永不静默吞错。
    """
    observations: list[dict] = []
    collected: list[dict] = []
    executed: list[ExecutedCall] = []
    for tool_call in tool_calls:
        function = tool_call["function"]
        name = function["name"]
        try:
            args = json.loads(function.get("arguments") or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments 不是 JSON 对象")
        except (json.JSONDecodeError, ValueError):
            observations.append(
                {"role": "tool", "tool_call_id": tool_call["id"], "content": f"参数非法:{function.get('arguments')!r}"}
            )
            continue
        if name not in visible:
            observations.append(
                {"role": "tool", "tool_call_id": tool_call["id"], "content": f"工具 {name} 不可用(未授权)"}
            )
            continue
        action = action_of(name)
        if classify_action(action) == "approval":
            try:
                snapshot = await executor.capture(action, args)
                collected.append({"action": action, "params": args, "snapshot": snapshot})
                observations.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": f"动作 {action} 已登记待人工审批(批准前不会生效)。现状快照:{snapshot}",
                    }
                )
            except Exception as error:
                observations.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": f"动作 {action} 登记失败:{error}",
                    }
                )
        else:
            try:
                result = await executor.execute(action, args)
                observations.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    }
                )
                executed.append(ExecutedCall(action=action, params=args, result=result))
            except Exception as error:
                observations.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": f"工具 {name} 执行失败:{error}",
                    }
                )
    return observations, collected, executed


def build_react_agent(
    *,
    name: str,
    system_prompt: str,
    registry: ToolRegistry,
    executor: Executor,
    llm: ToolCallingLlmClient,
    step_limit: int = 10,
) -> CompiledStateGraph:
    """ReAct 循环子图:agent(LLM 选工具)→ tools(执行/收集)→ agent … 直到作答或超限。

    agent 节点只暴露 registry 中授权给 "agent" 节点的工具(节点级动态绑定,B6)。
    """
    visible_names = {t.name for t in registry.visible_for("agent")}

    def _tools_for_llm() -> list[dict]:
        return [t.to_openai() for t in registry.visible_for("agent")]

    async def agent_node(state: AgentState) -> dict:
        if state.get("step_count", 0) >= step_limit:
            return {"incomplete": f"步数超限({step_limit}):任务未完成,如实终止"}
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": slice_prompt(state.get("task_request", ""), state["slice_description"])},
            *state.get("messages", []),
        ]
        result = await llm.complete_with_tools(messages, _tools_for_llm())
        step = {"step_count": state.get("step_count", 0) + 1}
        if result.tool_calls:
            return {**step, "messages": [assistant_message(result)], "tool_calls": result.tool_calls}
        return answer_turn(state, result, step)

    async def tool_node(state: AgentState) -> dict:
        observations, collected, executed = await resolve_tool_calls(
            state.get("tool_calls", []), visible=visible_names, executor=executor
        )
        # 检索类工具的命中累积进状态(非检索工具没有 hits 形状,天然落空)
        retrieval = retrieval_hits_from(executed)
        return {"messages": observations, "collected": collected, "retrieval": retrieval}

    def after_agent(state: AgentState) -> str:
        if state.get("incomplete") is not None or state.get("answer") is not None:
            return "end"
        if state.get("tool_calls"):
            return "tools"
        # 到此只剩一种情形:空正文的**重试轮**(#64 B1,agent 节点已把提示词追加进消息史)——
        # 显式回 agent 再问一次。不借道 tools 空跑一趟:重试是图上的一个落点,读这里就该看得见。
        return "retry"

    builder = StateGraph(AgentState)  # ty: ignore
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", after_agent, {"tools": "tools", "retry": "agent", "end": END})
    builder.add_edge("tools", "agent")
    return builder.compile(name=name)


AgentRunner = Callable[[Slice, str], Awaitable[dict]]


def make_agent_runner(graph: CompiledStateGraph) -> AgentRunner:
    """把编译好的业务子图包装为监督图的 AgentRunner(spec #7 挂接点)。

    第二参数 = **原始用户请求**(#64 B2):切片 Send 是完整替换状态,执行段看不到主 state,故由
    监督图显式传入;子图把它当「全局任务」摆在本切片职责之前(``slice_prompt``)。

    返回 {"actions": 收集的审批动作参数快照, "answer": 最终答复, "incomplete": 未完成原因|None,
    "citations": 引用条目(#51,随答案一起下发;无检索依据即空)}。

    子图内 LLM 失败(issue #10)在此单点收敛(三个业务 Agent 一次覆盖):与工具失败同策略,
    切片如实产出「未完成+原因」,不穿透为 REST 500、不触发重规划(重规划仅由人工拒绝触发);
    已收集的审批动作快照随之丢弃(切片判未完成,重新发起即可);编程错误继续上抛。
    """

    async def run(slice_: Slice, task_request: str) -> dict:
        try:
            final = await graph.ainvoke(AgentState(slice_description=slice_.description, task_request=task_request))
        except LlmFailure as error:
            # 子图**跑过**了(它失败了):executed 如实为真,未完成原因单独给(#64 A3 三态可辨)
            return {"actions": [], "answer": None, "incomplete": str(error), "citations": [], "executed": True}
        return {
            "actions": final.get("collected", []),
            "answer": final.get("answer"),
            "incomplete": final.get("incomplete"),
            "citations": final.get("citations") or [],
            "executed": True,
        }

    return run
