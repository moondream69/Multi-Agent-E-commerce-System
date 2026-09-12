"""共享 ReAct 子图构造器(spec #7):agent 节点 + tool 节点循环,步数上限 10(B17)。

工具按节点授权暴露(B6):agent 节点只看见本节点授权清单(LLM 看不到未授权工具);
tool 节点按「可见性 → 三层分类」裁决:不可见拒绝(边约束的兜底)/ approval 收集不执行
(诚实观察)/ auto 直行。resolve_tool_calls 为客服结构化图共用。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from python_backend.agents.executor import Executor, action_of
from python_backend.core.approvals import classify_action
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
    messages: Annotated[list[dict], merge_lists]
    step_count: int
    tool_calls: list[dict]
    collected: Annotated[list[dict], merge_lists]  # 审批动作参数快照(效果后置,切片边界打包)
    answer: str | None
    incomplete: str | None


async def resolve_tool_calls(
    tool_calls: list[dict], *, visible: set[str], executor: Executor
) -> tuple[list[dict], list[dict], list[tuple[str, dict]]]:
    """执行/收集一轮工具调用 → (观察消息, 收集的审批动作, 已执行的 auto 动作)。

    裁决顺序(B6/B7):可见性 → 三层分类;一切失败转诚实观察,永不静默吞错。
    """
    observations: list[dict] = []
    collected: list[dict] = []
    executed: list[tuple[str, dict]] = []
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
                executed.append((action, args))
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
            {"role": "user", "content": state["slice_description"]},
            *state.get("messages", []),
        ]
        result = await llm.complete_with_tools(messages, _tools_for_llm())
        step = {"step_count": state.get("step_count", 0) + 1}
        if result.tool_calls:
            return {**step, "messages": [assistant_message(result)], "tool_calls": result.tool_calls}
        # 作答轮须清空 tool_calls:该键无 reducer,上一轮的陈旧值会误导条件边
        return {
            **step,
            "messages": [assistant_message(result)],
            "tool_calls": [],
            "answer": result.content or "",
        }

    async def tool_node(state: AgentState) -> dict:
        observations, collected, _executed = await resolve_tool_calls(
            state.get("tool_calls", []), visible=visible_names, executor=executor
        )
        return {"messages": observations, "collected": collected}

    def after_agent(state: AgentState) -> str:
        if state.get("incomplete") is not None or state.get("answer") is not None:
            return "end"
        return "tools"

    builder = StateGraph(AgentState)  # ty: ignore
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", after_agent, {"tools": "tools", "end": END})
    builder.add_edge("tools", "agent")
    return builder.compile(name=name)


AgentRunner = Callable[[Slice], Awaitable[dict]]


def make_agent_runner(graph: CompiledStateGraph) -> AgentRunner:
    """把编译好的业务子图包装为监督图的 AgentRunner(spec #7 挂接点)。

    返回 {"actions": 收集的审批动作参数快照, "answer": 最终答复, "incomplete": 未完成原因|None}。

    子图内 LLM 失败(issue #10)在此单点收敛(三个业务 Agent 一次覆盖):与工具失败同策略,
    切片如实产出「未完成+原因」,不穿透为 REST 500、不触发重规划(重规划仅由人工拒绝触发);
    已收集的审批动作快照随之丢弃(切片判未完成,重新发起即可);编程错误继续上抛。
    """

    async def run(slice_: Slice) -> dict:
        try:
            final = await graph.ainvoke(AgentState(slice_description=slice_.description))
        except LlmFailure as error:
            return {"actions": [], "answer": None, "incomplete": str(error)}
        return {
            "actions": final.get("collected", []),
            "answer": final.get("answer"),
            "incomplete": final.get("incomplete"),
        }

    return run
