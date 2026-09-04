"""LangGraph 状态图实现 ReAct 循环(语义 1:1 对应 src/core/agent-base/react-loop.service.ts)。

图结构:
START → agent ──有 tool_calls─→ tools ──(round >= max)→ exhausted(抛错,由 BaseAgent 捕获→任务 FAILED)
           └──无 tool_calls─→ answer → END

workflow 激活时(Agent 声明了图级约束)增加两条边:
- agent 自环:阶段未完成时 LLM 直接输出 → 注入提示后强制回 agent 轮(不失败不终止)
- exhausted 直达:绕过自环的唯一出口(round >= max 时)

步骤(step)语义与 TS 版一致:
- 每轮推理: reasoning_N IN_PROGRESS(永不标完成)
- 工具执行: name IN_PROGRESS → name COMPLETED;未知/失败则 name FAILED 并附错误反馈继续
- 最终答案: final_answer COMPLETED
- workflow 注入: workflow_prompt COMPLETED;强制回轮: workflow_enforce COMPLETED
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from python_backend.core.workflow import Workflow, WorkflowPhase
from python_backend.domain.tasks import TaskStatus, ToolDefinition
from python_backend.domain.tools import ToolProtocol


class LlmLike(Protocol):
    """build_react_graph 只依赖 complete_with_tools,测试用 FakeLlm 替换真实服务。"""

    def complete_with_tools(self, messages: list[dict], tools: list[ToolDefinition]) -> Any: ...


_PHASE1_HINT = "[工作流提示] 当前阶段:处理用户问题前必须先调用工具 {tools}(顺序不限),未完成前禁止直接回答。"
_PHASE2_HINT = "[工作流提示] 必调工具已完成,现在可以使用全部工具,或直接给出最终回答。"
_REMIND_HINT = "[工作流提示] 你尚未调用必用工具:{tools}。请先调用后再给出最终回答。"


class AgentState(TypedDict):
    round: int
    messages: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    result: dict[str, Any] | None


def _now() -> datetime:
    return datetime.now(UTC)


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
    tools: Sequence[ToolProtocol],
    llm: LlmLike,
    max_iterations: int = 10,
    workflow: Workflow | None = None,
):
    tool_defs: list[ToolDefinition] = [t.definition for t in tools]
    tool_map: dict[str, ToolProtocol] = {t.definition.name: t for t in tools}
    if workflow is not None:
        for phase in workflow.phases:
            if phase.allowed_tools is not None and not phase.required_tools <= phase.allowed_tools:
                raise ValueError("workflow 阶段声明错误: required_tools 必须 ⊆ allowed_tools")

    def _called_tools(state: AgentState) -> set[str]:
        """遍历 messages,收集所有 assistant 消息 tool_calls 中出现过的工具名(OpenAI 格式)。"""
        called: set[str] = set()
        for m in state["messages"]:
            if m.get("role") == "assistant":
                for tc in m.get("tool_calls") or []:
                    name = (tc.get("function") or {}).get("name")
                    if name:
                        called.add(name)
        return called

    def _current_phase(called: set[str]) -> WorkflowPhase:
        """返回第一个 required_tools 未全覆盖的阶段;全部满足返回末阶段。"""
        assert workflow is not None
        for phase in workflow.phases:
            if not phase.required_tools <= called:
                return phase
        return workflow.phases[-1]

    def _workflow_done(called: set[str]) -> bool:
        assert workflow is not None
        return all(phase.required_tools <= called for phase in workflow.phases)

    async def agent_node(state: AgentState) -> dict[str, Any]:
        _add_step(
            state,
            f"reasoning_{state['round'] + 1}",
            TaskStatus.IN_PROGRESS,
            f"LLM 推理轮次 {state['round'] + 1}/{max_iterations}",
        )
        called = _called_tools(state)
        phase = _current_phase(called) if workflow is not None else None

        if phase is not None:
            has_assistant = any(m.get("role") == "assistant" for m in state["messages"])
            if phase.required_tools and not has_assistant:
                # 首轮注入阶段提示,让 LLM 少走弯路(提示词配合)
                state["messages"].append(
                    {"role": "user", "content": _PHASE1_HINT.format(tools="、".join(sorted(phase.required_tools)))}
                )
                _add_step(state, "workflow_prompt", TaskStatus.COMPLETED, "注入阶段必调工具提示")
            elif not phase.required_tools and not any(m.get("content") == _PHASE2_HINT for m in state["messages"]):
                # 阶段切换时注入一次解锁提示
                state["messages"].append({"role": "user", "content": _PHASE2_HINT})
                _add_step(state, "workflow_prompt", TaskStatus.COMPLETED, "注入阶段解锁提示")
            if phase.allowed_tools is not None:
                visible_tools = [d for d in tool_defs if d.name in phase.allowed_tools]
            else:
                visible_tools = tool_defs
        else:
            visible_tools = tool_defs
        # LLM 调用(LangChain 同步 invoke)卸载到线程池,避免阻塞事件循环(最长 60s)
        response = await asyncio.to_thread(llm.complete_with_tools, state["messages"], visible_tools)
        state["round"] += 1

        if not response.tool_calls:
            text = response.content or ""
            if not isinstance(text, str):
                text = json.dumps(text, ensure_ascii=False, default=str)
            if phase is not None and not phase.can_answer:
                # 绕过:保留文本、点名缺失工具、强制回轮;不失败、不终止
                missing = sorted(phase.required_tools - called)
                state["messages"].append({"role": "assistant", "content": text})
                state["messages"].append({"role": "user", "content": _REMIND_HINT.format(tools="、".join(missing))})
                _add_step(state, "workflow_enforce", TaskStatus.COMPLETED, f"阶段未完成,缺失工具: {'、'.join(missing)}")
            else:
                _add_step(state, "final_answer", TaskStatus.COMPLETED, text[:200])
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
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["args"], ensure_ascii=False),
                            },
                        }
                        for tc in response.tool_calls
                    ],
                }
            )
        return {
            "round": state["round"],
            "messages": state["messages"],
            "steps": state["steps"],
            "result": state["result"],
        }

    async def tools_node(state: AgentState) -> dict[str, Any]:
        tool_calls = state["messages"][-1]["tool_calls"]
        for tc in tool_calls:
            name = tc["function"]["name"]
            tool = tool_map.get(name)
            if tool is None:
                err_msg = f'工具 "{name}" 未找到'
                _add_step(state, name, TaskStatus.FAILED, err_msg)
                state["messages"].append({"role": "tool", "content": f"Error: {err_msg}", "tool_call_id": tc["id"]})
                continue
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
                _add_step(
                    state,
                    name,
                    TaskStatus.IN_PROGRESS,
                    f"执行 {name}({json.dumps(args, ensure_ascii=False)[:100]})",
                )
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
        if last.get("tool_calls"):
            return "tools"
        if workflow is not None:
            if state["round"] >= max_iterations:
                return "exhausted"  # 绕过自环的唯一出口
            if not _workflow_done(_called_tools(state)):
                return "agent"  # 阶段未完成:强制回 agent 轮
        return "answer"

    def route_tools(state: AgentState) -> str:
        return "exhausted" if state["round"] >= max_iterations else "agent"

    # ty 尚不能把 TypedDict 类识别为 langgraph 的 StateLike 协议成员,运行时没问题
    g = StateGraph[AgentState](AgentState)  # ty: ignore[invalid-type-arguments]
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.add_node("answer", answer_node)
    g.add_node("exhausted", exhausted_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges(
        "agent", route_agent, {"tools": "tools", "answer": "answer", "agent": "agent", "exhausted": "exhausted"}
    )
    g.add_conditional_edges("tools", route_tools, {"agent": "agent", "exhausted": "exhausted"})
    g.add_edge("answer", END)
    g.add_edge("exhausted", END)
    return g.compile()
