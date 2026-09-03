"""LangGraph 状态图实现 ReAct 循环(语义 1:1 对应 src/core/agent-base/react-loop.service.ts)。

图结构:
START → agent ──有 tool_calls─→ tools ──(round >= max)→ exhausted(抛错,由 BaseAgent 捕获→任务 FAILED)
           └──无 tool_calls─→ answer → END

步骤(step)语义与 TS 版一致:
- 每轮推理: reasoning_N IN_PROGRESS(永不标完成)
- 工具执行: name IN_PROGRESS → name COMPLETED;未知/失败则 name FAILED 并附错误反馈继续
- 最终答案: final_answer COMPLETED
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from python_backend.domain.tasks import TaskStatus, ToolDefinition
from python_backend.domain.tools import ToolProtocol
from python_backend.infrastructure.llm import LlmService


class AgentState(TypedDict):
    round: int
    messages: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    result: dict[str, Any] | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _add_step(state: AgentState, name: str, status: TaskStatus, detail: str) -> None:
    state["steps"].append(
        {
            "name": name,
            "status": status.value,
            "detail": detail,
            "startedAt": _now(),
            "completedAt": _now() if status == TaskStatus.COMPLETED else None,
        }
    )


def _show(value: Any) -> str:
    """把工具结果转成对话消息文本(与 TS 版一致:原始值转字符串,对象走 JSON)。"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def parse_final(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        return {"result": text}


def build_react_graph(
    system_prompt: str,
    tools: list[ToolProtocol],
    llm: LlmService,
    max_iterations: int = 10,
):
    tool_defs: list[ToolDefinition] = [t.definition for t in tools]
    tool_map: dict[str, ToolProtocol] = {t.definition.name: t for t in tools}

    async def agent_node(state: AgentState) -> dict[str, Any]:
        _add_step(state, f"reasoning_{state['round'] + 1}", TaskStatus.IN_PROGRESS, f"LLM 推理轮次 {state['round'] + 1}/{max_iterations}")
        # LlmService.complete_with_tools 为同步调用(LangChain invoke),无网络阻塞点之外的协程收益
        response = llm.complete_with_tools(state["messages"], tool_defs)
        state["round"] += 1

        if not response.tool_calls:
            text = response.content or ""
            _add_step(state, "final_answer", TaskStatus.COMPLETED, str(text)[:200])
            state["result"] = parse_final(text)
        else:
            state["messages"].append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": json.dumps(tc["args"], ensure_ascii=False)},
                        }
                        for tc in response.tool_calls
                    ],
                }
            )
        return {"round": state["round"], "messages": state["messages"], "steps": state["steps"], "result": state["result"]}

    async def tools_node(state: AgentState) -> dict[str, Any]:
        tool_calls = state["messages"][-1]["tool_calls"]
        for tc in tool_calls:
            name = tc["function"]["name"]
            tool = tool_map.get(name)
            if tool is None:
                err_msg = f"工具 \"{name}\" 未找到"
                _add_step(state, name, TaskStatus.FAILED, err_msg)
                state["messages"].append({"role": "tool", "content": f"Error: {err_msg}", "tool_call_id": tc["id"]})
                continue
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
                _add_step(state, name, TaskStatus.IN_PROGRESS, f"执行 {name}({json.dumps(args, ensure_ascii=False)[:100]})")
                result_str = _show(await tool.execute(args))
                _add_step(state, name, TaskStatus.COMPLETED, result_str[:200])
                state["messages"].append({"role": "tool", "content": result_str, "tool_call_id": tc["id"]})
            except Exception as error:
                err_msg = f"工具 {name} 执行失败: {error}"
                _add_step(state, name, TaskStatus.FAILED, err_msg)
                state["messages"].append({"role": "tool", "content": f"Error: {err_msg}", "tool_call_id": tc["id"]})
        return {"messages": state["messages"], "steps": state["steps"]}

    async def answer_node(state: AgentState) -> None:
        return None

    async def exhausted_node(state: AgentState) -> None:
        raise RuntimeError(f"ReAct 循环超出最大迭代次数 ({max_iterations})")

    def route_agent(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if last.get("tool_calls") else "answer"

    def route_tools(state: AgentState) -> str:
        return "exhausted" if state["round"] >= max_iterations else "agent"

    g = StateGraph(AgentState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.add_node("answer", answer_node)
    g.add_node("exhausted", exhausted_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route_agent, {"tools": "tools", "answer": "answer"})
    g.add_conditional_edges("tools", route_tools, {"agent": "agent", "exhausted": "exhausted"})
    g.add_edge("answer", END)
    g.add_edge("exhausted", END)
    return g.compile()
