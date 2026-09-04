"""客服 Workflow 图级强制测试:用脚本化 FakeLlm 驱动,验证两阶段工作流
(必调 sentiment_analysis + faq_search → 解锁全部工具)。

设计决策(与 grilling 达成):
- 阶段1 必调两工具,顺序不限;阶段2 白名单=全部工具、可自由回答
- 阶段未完成时 LLM 直接输出 → 注入点名缺失工具的提示后强制回 agent 轮(不失败不终止)
- 绕过自环的出口:workflow 激活时 round >= max_iterations → exhausted(死循环兜底)
- 曾试 tool_choice="required" 提升确定性,但 DeepSeek thinking 模式拒绝该参数(400),
  已回退:仅靠白名单裁剪 + 循环强制拉回
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from langchain_core.messages import AIMessage

from python_backend.agents.customer_service.agent import CustomerServiceAgent
from python_backend.agents.customer_service.tools import (
    EscalateTicketTool,
    FaqRetrievalTool,
    OrderLookupTool,
    SentimentAnalysisTool,
    TemplateManagerTool,
    TranslatorTool,
)
from python_backend.core.event_bus import EventBus
from python_backend.core.graph import build_react_graph
from python_backend.core.workflow import Workflow, WorkflowPhase
from python_backend.domain.tasks import AgentTask, TaskType, ToolDefinition, ToolParameter
from python_backend.domain.tools import ToolProtocol
from python_backend.infrastructure.llm import LlmService

WORKFLOW = Workflow(
    phases=(
        WorkflowPhase(
            required_tools=frozenset({"sentiment_analysis", "faq_search"}),
            allowed_tools=frozenset({"sentiment_analysis", "faq_search"}),
            can_answer=False,
        ),
        WorkflowPhase(required_tools=frozenset(), allowed_tools=None, can_answer=True),
    )
)


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(name=name, description=f"test {name}", parameters=[ToolParameter("x", "string", "测试参数")])


class FakeTool:
    """记录调用并返回设定结果。"""

    def __init__(self, definition: ToolDefinition, result: str | None = None) -> None:
        self.definition = definition
        self._result = result
        self.calls: list[dict] = []

    async def execute(self, params: dict) -> str:
        self.calls.append(params)
        return self._result or "ok"


class FakeLlm:
    """按脚本依次返回 AIMessage;脚本耗尽后重复最后一条。messages 记录快照(避免引用共享列表被后续 append 污染)。"""

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = script
        self.calls: list[tuple] = []

    def complete_with_tools(self, messages: list, tools: list) -> AIMessage:
        self.calls.append((list(messages), list(tools)))
        idx = min(len(self.calls) - 1, len(self._script) - 1)
        return self._script[idx]


def _tool_call_msg(name: str, args: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "call_1", "type": "function"}])


async def _run(
    llm: FakeLlm,
    tools: Sequence[ToolProtocol],
    max_iterations: int = 10,
    workflow: Workflow | None = WORKFLOW,
) -> dict:
    graph = build_react_graph("sys", tools, llm, max_iterations=max_iterations, workflow=workflow)
    return await graph.ainvoke(
        {"messages": [{"role": "user", "content": "hi"}], "steps": [], "round": 0, "result": None}
    )


def _reminders(messages: list[dict]) -> list[str]:
    return [m["content"] for m in messages if m.get("role") == "user" and "你尚未调用" in m["content"]]


async def test_phase1_bypass_forces_reminder_and_completes():
    tools = [FakeTool(_tool("sentiment_analysis")), FakeTool(_tool("faq_search"))]
    llm = FakeLlm(
        [
            _tool_call_msg("sentiment_analysis", {"text": "x"}),
            AIMessage(content="我先直接回答了"),
            _tool_call_msg("faq_search", {"question": "x"}),
            AIMessage(content='{"result": "完成"}'),
        ]
    )
    trace = await _run(llm, tools)
    assert len(llm.calls) == 4
    assert trace["result"] == {"result": "完成"}
    reminders = _reminders(trace["messages"])
    assert reminders and "faq_search" in reminders[0]
    assert any(s["name"] == "workflow_enforce" for s in trace["steps"])


async def test_phase1_bypass_exhausts_rounds():
    tools = [FakeTool(_tool("sentiment_analysis")), FakeTool(_tool("faq_search"))]
    llm = FakeLlm([AIMessage(content="就是不想调工具")])
    with pytest.raises(RuntimeError, match="ReAct 循环超出最大迭代次数"):
        await _run(llm, tools, max_iterations=3)


async def test_phase1_whitelist_filters_tools():
    tools = [FakeTool(_tool(n)) for n in ("sentiment_analysis", "faq_search", "translate", "manage_template")]
    llm = FakeLlm(
        [
            _tool_call_msg("sentiment_analysis", {"text": "x"}),
            _tool_call_msg("faq_search", {"question": "x"}),
            AIMessage(content='{"result": "完成"}'),
        ]
    )
    await _run(llm, tools)
    assert {t.name for t in llm.calls[0][1]} == {"sentiment_analysis", "faq_search"}
    assert {t.name for t in llm.calls[1][1]} == {"sentiment_analysis", "faq_search"}
    assert {t.name for t in llm.calls[2][1]} == {"sentiment_analysis", "faq_search", "translate", "manage_template"}


async def test_phase1_order_is_free():
    tools = [FakeTool(_tool("sentiment_analysis")), FakeTool(_tool("faq_search"))]
    llm = FakeLlm(
        [
            _tool_call_msg("faq_search", {"question": "x"}),
            _tool_call_msg("sentiment_analysis", {"text": "x"}),
            AIMessage(content='{"result": "完成"}'),
        ]
    )
    trace = await _run(llm, tools)
    assert trace["result"] == {"result": "完成"}
    assert _reminders(trace["messages"]) == []


async def test_phase1_hint_injected_only_on_first_round():
    tools = [FakeTool(_tool("sentiment_analysis")), FakeTool(_tool("faq_search"))]
    llm = FakeLlm(
        [
            _tool_call_msg("sentiment_analysis", {"text": "x"}),
            _tool_call_msg("faq_search", {"question": "x"}),
            AIMessage(content='{"result": "完成"}'),
        ]
    )
    await _run(llm, tools)
    hint0 = llm.calls[0][0]
    assert any(m.get("role") == "user" and "必须先调用工具" in m["content"] for m in hint0)
    phase1_text = next(m["content"] for m in hint0 if "必须先调用工具" in m["content"])
    assert "sentiment_analysis" in phase1_text and "faq_search" in phase1_text
    # 每轮快照中阶段1提示恰好出现一次(首轮注入后不再重复)
    for snapshot in (llm.calls[0][0], llm.calls[1][0], llm.calls[2][0]):
        count = sum(1 for m in snapshot if m.get("role") == "user" and "必须先调用工具" in m["content"])
        assert count == 1


async def test_phase2_free_answer_terminates():
    tools = [FakeTool(_tool("sentiment_analysis")), FakeTool(_tool("faq_search"))]
    llm = FakeLlm(
        [
            _tool_call_msg("sentiment_analysis", {"text": "x"}),
            _tool_call_msg("faq_search", {"question": "x"}),
            AIMessage(content="自由回答"),
        ]
    )
    trace = await _run(llm, tools)
    assert trace["result"] == {"result": "自由回答"}
    assert len(llm.calls) == 3
    assert _reminders(llm.calls[2][0]) == []


async def test_workflow_none_behavior_unchanged():
    llm = FakeLlm([AIMessage(content="这不是JSON")])
    trace = await _run(llm, [], workflow=None)
    assert trace["result"] == {"result": "这不是JSON"}


async def test_customer_service_execute_task_enforces_workflow():
    agent = CustomerServiceAgent(
        EventBus(),
        LlmService(),
        TranslatorTool(LlmService()),
        FaqRetrievalTool(),
        SentimentAnalysisTool(LlmService()),
        TemplateManagerTool(),
        OrderLookupTool(),
        EscalateTicketTool(),
    )
    agent.tools = [FakeTool(_tool("sentiment_analysis")), FakeTool(_tool("faq_search"))]
    llm = FakeLlm(
        [
            AIMessage(content="我先回答了"),
            _tool_call_msg("sentiment_analysis", {"text": "x"}),
            _tool_call_msg("faq_search", {"question": "x"}),
            AIMessage(content='{"result": "客服回复完成"}'),
        ]
    )
    agent._llm = llm  # type: ignore
    agent._graph = None
    task = AgentTask(id="t1", type=TaskType.CUSTOMER_SERVICE, input={"action": "handle_query", "text": "如何退货?"})
    result = await agent.execute_task(task)
    assert result == {"result": "客服回复完成"}
    assert len(llm.calls) == 4
