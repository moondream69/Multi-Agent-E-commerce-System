"""业务子图测试(spec #7 seam 1):工具暴露(B6)、执行行为(B7 收集/免审直行)、
客服查证优先边约束(B12)、步数上限(B17)。全部不依赖真实 LLM/DB。"""

from __future__ import annotations

import json

from python_backend.agents.base import AgentState, build_react_agent
from python_backend.agents.customer_service.agent import build_customer_agent
from python_backend.agents.executor import action_of
from python_backend.agents.order_management.agent import build_order_agent
from python_backend.agents.product_research.agent import build_product_agent
from python_backend.core.approvals import classify_action
from python_backend.domain.tools import ToolDefinition, ToolRegistry
from python_backend.infrastructure.llm import ToolCallResult
from tests.conftest import FakeExecutor, FakeLlm


def tool(name: str, description: str = "") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description or f"{name} 工具",
        parameters={"type": "object", "properties": {}, "required": []},
    )


def registry(*names: str) -> ToolRegistry:
    reg = ToolRegistry()
    for name in names:
        reg.register(tool(name), authorized_nodes={"agent"})
    return reg


def call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def round_tools(*calls: dict) -> ToolCallResult:
    return ToolCallResult(content="", tool_calls=list(calls))


def round_text(content: str) -> ToolCallResult:
    return ToolCallResult(content=content, tool_calls=[])


async def run(graph, description: str = "测试切片") -> dict:
    return await graph.ainvoke(AgentState(slice_description=description))


# —— B6:工具暴露(agent 节点只看见授权清单)——


async def test_llm_only_sees_authorized_tools() -> None:
    llm = FakeLlm(tool_rounds=[round_text("完成")])
    graph = build_react_agent(
        name="order_management",
        system_prompt="你是订单助手",
        registry=registry("list_orders", "product_publish"),
        executor=FakeExecutor(),
        llm=llm,
    )
    await run(graph)
    visible = [t["function"]["name"] for t in llm.calls[0]["tools"]]
    assert visible == ["list_orders", "product_publish"]


# —— B7:审批动作收集不执行,免审动作直行 ——


async def test_approval_tool_captured_not_executed() -> None:
    llm = FakeLlm(
        tool_rounds=[
            round_tools(call("product_publish", {"product_id": 5})),
            round_text("上架动作已登记,等待您的批准。"),
        ]
    )
    executor = FakeExecutor(snapshots={"product.publish": {"exists": True, "status": "draft"}})
    graph = build_react_agent(
        name="order_management",
        system_prompt="你是订单助手",
        registry=registry("product_publish"),
        executor=executor,
        llm=llm,
    )
    result = await run(graph)

    assert result["collected"] == [
        {
            "action": "product.publish",
            "params": {"product_id": 5},
            "snapshot": {"exists": True, "status": "draft"},
        }
    ]
    assert executor.executed == [], "审批动作不得执行"
    assert executor.captured == [("product.publish", {"product_id": 5})]
    assert result["answer"] == "上架动作已登记,等待您的批准。"


async def test_auto_tool_executes_and_observation_feeds_back() -> None:
    llm = FakeLlm(
        tool_rounds=[
            round_tools(call("list_orders", {})),
            round_text("共 3 笔订单。"),
        ]
    )
    executor = FakeExecutor(results={"list_orders": {"count": 3}})
    graph = build_react_agent(
        name="order_management",
        system_prompt="你是订单助手",
        registry=registry("list_orders"),
        executor=executor,
        llm=llm,
    )
    result = await run(graph)

    assert executor.executed == [("list_orders", {})]
    assert result["collected"] == []
    # 观察回喂:第二轮 LLM 消息含工具结果
    second_round_messages = llm.calls[1]["messages"]
    assert any("count" in str(m.get("content")) for m in second_round_messages if m["role"] == "tool")


async def test_tool_node_rejects_call_outside_visible_set() -> None:
    """B6:即便 LLM 脚本化调用不可见工具,tool 节点拒绝(不可用观察),不执行不收集。"""
    llm = FakeLlm(tool_rounds=[round_tools(call("product_delete", {"product_id": 5})), round_text("完成")])
    executor = FakeExecutor()
    graph = build_react_agent(
        name="order_management",
        system_prompt="你是订单助手",
        registry=registry("list_orders"),  # product_delete 不在授权集
        executor=executor,
        llm=llm,
    )
    result = await run(graph)

    assert any("不可用" in m.get("content", "") for m in result["messages"])
    assert executor.executed == [] and executor.captured == []
    assert result["collected"] == []


async def test_tool_failure_is_honest_observation() -> None:
    """工具失败:诚实观察回喂 LLM,永不静默吞错。"""

    class FailingExecutor(FakeExecutor):
        async def execute(self, action: str, params: dict) -> dict:
            raise ValueError("数据库不可达")

    llm = FakeLlm(tool_rounds=[round_tools(call("list_orders", {})), round_text("查询失败,请稍后再试。")])
    graph = build_react_agent(
        name="order_management",
        system_prompt="你是订单助手",
        registry=registry("list_orders"),
        executor=FailingExecutor(),
        llm=llm,
    )
    result = await run(graph)

    assert any("失败" in m.get("content", "") and "数据库不可达" in m.get("content", "") for m in result["messages"])


# —— B17:步数上限 ——


async def test_react_step_limit_produces_incomplete() -> None:
    llm = FakeLlm(tool_rounds=[round_tools(call("list_orders", {}))])
    graph = build_react_agent(
        name="order_management",
        system_prompt="你是订单助手",
        registry=registry("list_orders"),
        executor=FakeExecutor(),
        llm=llm,
        step_limit=3,
    )
    result = await run(graph)

    assert result["incomplete"] is not None
    assert "超限" in result["incomplete"]
    assert result.get("answer") is None


# —— B12:客服查证优先(图级边约束)——


async def test_customer_verify_tools_only_in_verify_node() -> None:
    _graph, reg = build_customer_agent(executor=FakeExecutor(), llm=FakeLlm())
    assert [t.name for t in reg.visible_for("verify")] == ["faq_search", "order_lookup", "product_lookup"]
    draft_tools = [t.name for t in reg.visible_for("draft")]
    assert "faq_search" not in draft_tools and "order_lookup" not in draft_tools
    assert "product_lookup" not in draft_tools
    assert "generate_draft" in draft_tools


async def test_customer_draft_unreachable_without_evidence() -> None:
    """B12:未查证(evidence 0)时 draft 不可达——循环被拉回 verify,最终步数超限终止。"""
    llm = FakeLlm(tool_rounds=[round_text("")])  # 永不调用查证工具
    graph, _ = build_customer_agent(executor=FakeExecutor(), llm=llm, step_limit=3)
    result = await run(graph)

    assert result.get("answer") is None, "未查证不得产出草稿"
    assert result.get("evidence_count", 0) == 0
    assert result["incomplete"] is not None  # 图级约束使其无法前进,最终按上限终止


async def test_customer_verify_then_draft_flow() -> None:
    """查证 → 草稿:B12 约束满足后 draft 可达,产出最终草稿。"""
    llm = FakeLlm(
        tool_rounds=[
            round_tools(call("faq_search", {"query": "退货"}), call("order_lookup", {"order_id": 1}, "call_2")),
            round_text(""),
            round_text("尊敬的客户,经查证您的订单已发货,预计 3 天送达。"),
        ]
    )
    executor = FakeExecutor(results={"faq_search": {"hits": []}, "order_lookup": {"status": "shipped"}})
    graph, _ = build_customer_agent(executor=executor, llm=llm)
    result = await run(graph)

    assert result["evidence_count"] == 2
    assert result["answer"] == "尊敬的客户,经查证您的订单已发货,预计 3 天送达。"
    assert ("faq_search", {"query": "退货"}) in executor.executed
    assert ("order_lookup", {"order_id": 1}) in executor.executed


async def test_customer_product_lookup_counts_as_evidence_and_reaches_draft() -> None:
    """issue #38:商品查证计入 B12 证据集合——买家商品类提问(product_lookup)查完即可进草稿。"""
    llm = FakeLlm(
        tool_rounds=[
            round_tools(call("product_lookup", {"title": "咖啡"})),
            round_text(""),
            round_text("亲,该商品售价 129.00 元,现货充足。"),
        ]
    )
    executor = FakeExecutor(results={"product_lookup": {"matches": [{"id": 7, "title": "挂耳咖啡"}]}})
    graph, _ = build_customer_agent(executor=executor, llm=llm)
    result = await run(graph)

    assert result["evidence_count"] == 1
    assert result["answer"] == "亲,该商品售价 129.00 元,现货充足。"
    assert ("product_lookup", {"title": "咖啡"}) in executor.executed


async def test_customer_replayed_assistant_messages_keep_reasoning_content() -> None:
    """issue #27:回灌的助手消息须原样带 reasoning_content(思考模式下缺它 → provider 400)。

    触发位 = verify→draft:verify 的结论助手消息成为 draft 首轮的最后一条消息。
    """
    llm = FakeLlm(
        tool_rounds=[
            ToolCallResult(
                content="", tool_calls=[call("faq_search", {"query": "退货"})], reasoning_content="查知识库"
            ),
            ToolCallResult(content="查证结论:无命中", tool_calls=[], reasoning_content="证据不足,如实说明"),
            ToolCallResult(content="草稿正文", tool_calls=[], reasoning_content="起草"),
        ]
    )
    graph, _ = build_customer_agent(executor=FakeExecutor(results={"faq_search": {"hits": []}}), llm=llm)
    await run(graph)

    draft_messages = llm.calls[-1]["messages"]  # 最后一次调用 = draft 首轮
    assistants = [m for m in draft_messages if m["role"] == "assistant"]
    assert [m["reasoning_content"] for m in assistants] == ["查知识库", "证据不足,如实说明"]


async def test_customer_assistant_message_keeps_empty_reasoning_content() -> None:
    """issue #27:空串 reasoning_content 也须保留该键(provider 明示空串同样要求回传)——真值判断会漏掉它。"""
    empty = ToolCallResult(content="", tool_calls=[call("faq_search", {"query": "退货"})], reasoning_content="")
    llm = FakeLlm(
        tool_rounds=[
            empty,
            ToolCallResult(content="查证结论", tool_calls=[], reasoning_content=""),
            ToolCallResult(content="草稿正文", tool_calls=[], reasoning_content=""),
        ]
    )
    graph, _ = build_customer_agent(executor=FakeExecutor(results={"faq_search": {"hits": []}}), llm=llm)
    await run(graph)

    assistants = [m for m in llm.calls[-1]["messages"] if m["role"] == "assistant"]
    assert [m["reasoning_content"] for m in assistants] == ["", ""]


async def test_react_replayed_assistant_messages_keep_reasoning_content() -> None:
    """issue #27:ReAct 线与客服线共用同一助手消息构造,同样不得丢 reasoning_content。"""
    llm = FakeLlm(
        tool_rounds=[
            ToolCallResult(content="", tool_calls=[call("lookup", {})], reasoning_content="先查"),
            ToolCallResult(content="完成", tool_calls=[], reasoning_content="作答"),
        ]
    )
    graph = build_react_agent(
        name="order_management",
        system_prompt="测试",
        registry=registry("lookup"),
        executor=FakeExecutor(results={"lookup": {"ok": True}}),
        llm=llm,
    )
    await run(graph)

    assistants = [m for m in llm.calls[-1]["messages"] if m["role"] == "assistant"]
    assert [m["reasoning_content"] for m in assistants] == ["先查"]


# —— 工具清单与动作映射(B7:禁做不存在)——


async def test_every_agent_tool_maps_to_known_classification() -> None:
    """所有暴露工具的动作标识都有分类,且无 forbidden(禁做工具不存在)。"""
    _, order_reg = build_order_agent(executor=FakeExecutor(), llm=FakeLlm())
    _, product_reg = build_product_agent(executor=FakeExecutor(), llm=FakeLlm())
    _, customer_reg = build_customer_agent(executor=FakeExecutor(), llm=FakeLlm())
    for reg in (order_reg, product_reg, customer_reg):
        for node in ("agent", "verify", "draft"):
            for definition in reg.visible_for(node):
                assert classify_action(action_of(definition.name)) != "forbidden", definition.name


async def test_order_agent_exposes_approval_tools() -> None:
    _, reg = build_order_agent(executor=FakeExecutor(), llm=FakeLlm())
    names = [t.name for t in reg.visible_for("agent")]
    assert "product_publish" in names
    assert "order_transition" in names
    assert "order_create" in names  # spec #8:order.create 审批工具(扣真实库存,批准后 apply)


async def test_action_of_mapping_is_deterministic() -> None:
    assert action_of("product_publish") == "product.publish"
    assert action_of("draft_create") == "draft.create"
    assert action_of("order_cancel") == "order.cancel"
