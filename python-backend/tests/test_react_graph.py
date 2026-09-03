"""ReAct 状态图行为测试:用脚本化 FakeLLM 驱动,验证与 TS ReAct 循环的语义一致。"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from python_backend.core.graph import build_react_graph
from python_backend.domain.tasks import ToolDefinition, ToolParameter


def _tool(name: str, results: list | None = None) -> ToolDefinition:
    return ToolDefinition(name=name, description=f"test {name}", parameters=[ToolParameter("x", "string", "测试参数")])


class FakeTool:
    """记录调用并返回设定结果,可要求抛错。"""

    def __init__(self, definition: ToolDefinition, result: str | None = None, error: Exception | None = None) -> None:
        self.definition = definition
        self._result = result
        self._error = error
        self.calls: list[dict] = []

    async def execute(self, params: dict) -> str:
        self.calls.append(params)
        if self._error:
            raise self._error
        return self._result or "ok"


class FakeLlm:
    """按脚本依次返回 AIMessage;脚本耗尽后重复最后一条。"""

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = script
        self.calls: list[tuple] = []

    def complete_with_tools(self, messages: list, tools: list) -> AIMessage:
        self.calls.append((messages, tools))
        idx = min(len(self.calls) - 1, len(self._script) - 1)
        return self._script[idx]


def _tool_call_msg(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "function"}])


async def test_tool_flow_then_final_answer():
    tool = FakeTool(_tool("do_a"), result="工具结果")
    llm = FakeLlm([_tool_call_msg("do_a", {"x": "1"}), AIMessage(content='{"result": "完成"}')])
    graph = build_react_graph("sys", [tool], llm)
    trace = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "hi"}], "steps": [], "round": 0, "result": None}
    )
    assert len(tool.calls) == 1
    assert trace["result"] == {"result": "完成"}
    names = [s["name"] for s in trace["steps"]]
    assert "reasoning_1" in names and "final_answer" in names
    # 工具步骤:IN_PROGRESS 与 COMPLETED 两条、以及 reasoning 保持 in_progress
    assert [s["status"] for s in trace["steps"] if s["name"] == "do_a"] == ["in_progress", "completed"]
    assert trace["steps"][0]["status"] == "in_progress"  # reasoning_1 永不标完成


async def test_unknown_tool_feeds_error_back():
    llm = FakeLlm([_tool_call_msg("nope", {}), AIMessage(content="继续")])
    graph = build_react_graph("sys", [], llm)
    trace = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "hi"}], "steps": [], "round": 0, "result": None}
    )
    # 第二条消息是错误反馈
    tool_msg = trace["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert "未找到" in tool_msg["content"]
    assert any(s["status"] == "failed" for s in trace["steps"])


async def test_tool_exception_feeds_error_back():
    tool = FakeTool(_tool("boom"), error=ValueError("坏掉了"))
    llm = FakeLlm([_tool_call_msg("boom", {}), AIMessage(content="继续")])
    graph = build_react_graph("sys", [tool], llm)
    trace = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "hi"}], "steps": [], "round": 0, "result": None}
    )
    tool_msg = trace["messages"][-1]
    assert "执行失败" in tool_msg["content"] and "坏掉了" in tool_msg["content"]


async def test_final_answer_fallback_to_result_field():
    llm = FakeLlm([AIMessage(content="这不是JSON")])
    graph = build_react_graph("sys", [], llm)
    trace = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "hi"}], "steps": [], "round": 0, "result": None}
    )
    assert trace["result"] == {"result": "这不是JSON"}


async def test_max_iterations_raises():
    llm = FakeLlm([_tool_call_msg("do_a", {})])
    tool = FakeTool(_tool("do_a"))
    graph = build_react_graph("sys", [tool], llm, max_iterations=3)
    with pytest.raises(RuntimeError, match="ReAct 循环超出最大迭代次数"):
        await graph.ainvoke(
            {"messages": [{"role": "user", "content": "hi"}], "steps": [], "round": 0, "result": None}
        )
