"""切片证据块(#69):系统记录类查证的采集、裁剪与出块——两线共用,全程离线(假执行器/假 LLM)。

依据(2026-09-17 实评 ``run-20260917T134340Z``):``cs-task-returns-zh#3#3`` 判 0,判词「产出未引用
任何查证条目,却给出 SYN-ORD-00001 智能插座 深空黑款…」。实核快照:该片 ``agent = order_management``
——订单线**没有** order_lookup,它靠 ``list_orders`` 读回订单事实,而查回的值不进切片产出
⇒ 商品/订单类结论在判分材料里结构性不可核验(与 #67 收口前的工作台线同病根,工作台线已补)。
"""

from __future__ import annotations

import json

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from python_backend.agents.base import (
    AgentState,
    ExecutedCall,
    build_react_agent,
    evidence_block_from,
    evidence_calls_from,
    make_agent_runner,
    retrieval_ids,
)
from python_backend.agents.customer_service.agent import CustomerState, build_customer_agent
from python_backend.agents.order_management.agent import build_order_agent
from python_backend.core.graph import SupervisorState, build_supervisor
from python_backend.core.planning import Slice, SlicePlan
from python_backend.domain.tools import ToolDefinition, ToolRegistry
from python_backend.infrastructure.llm import ToolCallResult
from tests.conftest import FakeApply, FakeExecutor, FakeLlm, InMemoryApprovalBatchStore

ORDER_ROW = {"reference": "SYN-ORD-00001", "status": "delivered"}


def call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def round_tools(*calls: dict) -> ToolCallResult:
    return ToolCallResult(content="", tool_calls=list(calls))


def round_text(content: str) -> ToolCallResult:
    return ToolCallResult(content=content, tool_calls=[])


# —— 采集面:只收系统记录类 ——


def test_evidence_collects_only_system_record_lookups() -> None:
    """收口面写死在三类动作:检索类命中归 citations(#64 A1 给切块正文),别的一律不进块。

    收窄而非「凡是 auto 动作都收」:选品线/规划线只有检索类工具 ⇒ 那些线的判分材料**零变化**,
    不会拿新物料去扰动已经过线的判据。
    """
    executed = [
        ExecutedCall(action="faq_search", params={"query": "退货"}, result={"hits": [{"id": "f1"}]}),
        ExecutedCall(action="order_lookup", params={"order_id": 1}, result=ORDER_ROW),
        ExecutedCall(action="product_lookup", params={"sku": "S-1"}, result={"matches": [], "truncated": False}),
        ExecutedCall(action="list_orders", params={}, result=[ORDER_ROW]),
        ExecutedCall(action="translate", params={"text": "hi"}, result={"translated": "你好"}),
    ]

    entries = evidence_calls_from(executed)

    assert [entry["tool"] for entry in entries] == ["order_lookup", "product_lookup", "list_orders"]
    assert entries[0] == {"tool": "order_lookup", "params": {"order_id": 1}, "result": ORDER_ROW}


def test_no_system_record_lookup_means_no_block() -> None:
    """没有查证即 None(如实缺席,判分材料不渲染该段)——「查过没查到」另有其形状。"""
    assert evidence_block_from([], "答案") is None


def test_bounded_results_pass_through_untouched() -> None:
    """有界结果原样落块:单条记录/短列表不加 truncated 之类的噪声。"""
    block = evidence_block_from(
        [
            {"tool": "order_lookup", "params": {"order_id": 1}, "result": ORDER_ROW},
            {
                "tool": "product_lookup",
                "params": {"sku": "S-1"},
                "result": {"matches": [{"id": 7}], "truncated": False},
            },
        ],
        "订单 SYN-ORD-00001 已送达",
    )

    assert block == {
        "lookups": [
            {"tool": "order_lookup", "params": {"order_id": 1}, "result": ORDER_ROW},
            {
                "tool": "product_lookup",
                "params": {"sku": "S-1"},
                "result": {"matches": [{"id": 7}], "truncated": False},
            },
        ]
    }


def test_unbounded_list_keeps_mentioned_row_then_head_and_annotates() -> None:
    """无界列表(list_orders 读全库):产出点名的行必在,其余补头部,总行数如实标注。

    实测背景:播种使 created_at 并列 ⇒ 「按时间倒序」实为并列,截前 N 行不是有意义的样本
    (实测点名的那单排在第 218 位)。故先按产出提及挑行,再补头部。
    """
    rows = [{"reference": f"SYN-ORD-{index:05d}", "status": "delivered"} for index in range(1, 2001)]

    block = evidence_block_from(
        [{"tool": "list_orders", "params": {}, "result": rows}],
        "比如 SYN-ORD-00218(智能插座 深空黑款)目前是 delivered",
    )

    assert block is not None
    [entry] = block["lookups"]
    kept = [row["reference"] for row in entry["result"]]
    assert "SYN-ORD-00218" in kept  # 产出点名的那单在材料里(判据要核的就是它)
    assert kept[0] == "SYN-ORD-00001"  # 其余按原顺序补头部
    assert len(kept) == 20
    assert entry["truncated"] is True
    assert entry["rows_total"] == 2000


# —— 两线出块:ReAct(订单/选品)与客服结构化图 ——


async def test_order_slice_runner_returns_evidence_block() -> None:
    """订单线:list_orders 读回的事实随切片产出出块——#3#3 那片的病根正在这条线上。"""
    llm = FakeLlm(tool_rounds=[round_tools(call("list_orders", {})), round_text("SYN-ORD-00001 目前是 delivered")])
    executor = FakeExecutor(results={"list_orders": [ORDER_ROW]})
    graph, _ = build_order_agent(executor=executor, llm=llm)

    result = await make_agent_runner(graph)(Slice(no=3, agent="order_management", description="查订单状态"), "")

    assert result["evidence"] == {
        "lookups": [{"tool": "list_orders", "params": {}, "result": [ORDER_ROW]}],
    }


async def test_customer_verify_results_land_in_evidence_block() -> None:
    """客服线:verify 的查回值(而非「调用记录」)进块——B12 的判定仍看 evidence_count。"""
    llm = FakeLlm(
        tool_rounds=[
            round_tools(call("order_lookup", {"order_id": 1})),
            round_text(""),
            round_text("您的订单已送达。"),
        ]
    )
    executor = FakeExecutor(results={"order_lookup": ORDER_ROW})
    graph, _ = build_customer_agent(executor=executor, llm=llm)

    result = await make_agent_runner(graph)(Slice(no=1, agent="customer_service", description="查订单"), "")

    assert result["evidence"] == {"lookups": [{"tool": "order_lookup", "params": {"order_id": 1}, "result": ORDER_ROW}]}
    assert result["answer"] == "您的订单已送达。"


async def test_react_slice_without_lookup_carries_no_evidence() -> None:
    """选品线(只有检索类工具)物料的零变化保证:没有系统记录查证即 None。"""
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="trend_query",
            description="情报检索",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        authorized_nodes={"agent"},
    )
    llm = FakeLlm(
        tool_rounds=[
            round_tools(call("trend_query", {"query": "智能家居"})),
            round_text("美国智能家居 2016 年 104 亿美元 [usitc-global-digital-trade-1#583]"),
        ]
    )
    executor = FakeExecutor(
        results={
            "trend_query": {
                "hits": [
                    {
                        "id": "usitc-global-digital-trade-1#583",
                        "score": 0.9,
                        "payload": {"content": "2016 年美国智能家居市场规模 104 亿美元。"},
                    }
                ]
            }
        }
    )
    graph = build_react_agent(
        name="product_research", system_prompt="你是选品", registry=registry, executor=executor, llm=llm
    )

    result = await make_agent_runner(graph)(Slice(no=1, agent="product_research", description="趋势"), "")

    assert result["evidence"] is None


# —— 监督图贯通:merged(get_task 的 results)与批次 run_output(durable 重放)两份都要有 ——

APPROVE_ACTION = {"action": "product.publish", "params": {"product_id": 1}, "snapshot": {"exists": True}}


class _SinglePlan:
    """固定单切片计划(本模块只需要一条切片穿过监督图)。"""

    async def plan(self, request: str, context: str | None = None) -> SlicePlan:
        return SlicePlan(slices=[Slice(no=1, agent="order_management", description="查订单状态")])


def _evidence_runner():
    """脚本化切片运行器:带审批动作 + 系统记录查证结果块(#69 的两种载荷同时在场)。"""

    async def run(slice_: Slice, _task_request: str) -> dict:
        return {
            "agent": slice_.agent,
            "description": slice_.description,
            "executed": True,
            "actions": [APPROVE_ACTION],
            "evidence": {"lookups": [{"tool": "order_lookup", "params": {"order_id": 1}, "result": ORDER_ROW}]},
        }

    return run


async def _approve_pending(graph, config: dict) -> None:
    snapshot = await graph.aget_state(config)
    interrupt_ = snapshot.tasks[0].interrupts[0]
    batch_id = interrupt_.value["batches"][0]["batch_id"]
    await graph.ainvoke(
        Command(resume={interrupt_.id: {"terminate": False, "decisions": {batch_id: {"decision": "approve"}}}}),
        config,
    )


async def test_supervisor_carries_evidence_into_results_and_batch_row() -> None:
    """证据块随切片产出下发(get_task 的 results),并进批次行(durable 重放不丢这份数据)。"""
    store = InMemoryApprovalBatchStore()
    graph = build_supervisor(
        _SinglePlan(),
        agents={"order_management": _evidence_runner()},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=FakeApply(),
    )
    config = {"configurable": {"thread_id": "evidence-block"}}

    await graph.ainvoke(SupervisorState(request="查订单", thread_id=config["configurable"]["thread_id"]), config)
    await _approve_pending(graph, config)

    final = await graph.aget_state(config)
    assert final.values["results"][1]["evidence"]["lookups"][0]["tool"] == "order_lookup"
    assert store.batches[0].run_output is not None
    assert store.batches[0].run_output["evidence"]["lookups"][0]["result"] == ORDER_ROW


def test_state_keys_declared() -> None:
    """状态键声明守卫:客服线的调用记录改名 verify_calls 后,两个状态各有其键(写错键名即在此暴露)。"""
    assert "evidence_calls" in AgentState.__annotations__
    assert "verify_calls" in CustomerState.__annotations__
    assert "evidence_calls" in CustomerState.__annotations__


# —— 检索命中池(#75 B):只落 id、去重保序,随切片产出下发 ——


def test_retrieval_ids_dedupes_and_keeps_order() -> None:
    """命中池 → 去重保序的标识清单(多次检索命中同块只记一次;正文不落,快照只收 id)。"""
    hits = [
        {"id": "d#1", "score": 0.9, "payload": {"content": "…"}},
        {"id": "d#2", "score": 0.8},
        {"id": "d#1", "score": 0.7},  # 第二次检索又命中同一块
    ]

    assert retrieval_ids(hits) == ["d#1", "d#2"]
    assert retrieval_ids([]) == []


async def test_product_slice_runner_returns_retrieval_hit_ids() -> None:
    """选品线:一次历实检索到**两块**、答案只引了其中一块 ⇒ 命中池两块都在。

    这正是 #75 B 要分开的那件事:``citations`` 只收被引的块(答案里出现过标记的),
    「检索到了但没引」原先不落任何地方,与「凭自身记忆补写」在快照上同形
    (``smart-band-us#3#2`` 分诊时只能逐块核对才敢下结论)。
    """
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(name="trend_query", description="情报检索", parameters={"type": "object", "properties": {}}),
        authorized_nodes={"agent"},
    )
    chunks = [
        {
            "id": "usitc-global-digital-trade-1#583",
            "score": 0.9,
            "payload": {"content": "2016 年美国智能家居 104 亿美元。"},
        },
        {"id": "usitc-global-digital-trade-1#913", "score": 0.6, "payload": {"content": "书目条目。"}},
    ]
    llm = FakeLlm(
        tool_rounds=[
            round_tools(call("trend_query", {"query": "智能家居"})),
            round_text("美国智能家居 2016 年 104 亿美元 [usitc-global-digital-trade-1#583]"),
        ]
    )
    executor = FakeExecutor(results={"trend_query": {"hits": chunks}})
    graph = build_react_agent(
        name="product_research", system_prompt="你是选品", registry=registry, executor=executor, llm=llm
    )

    result = await make_agent_runner(graph)(Slice(no=1, agent="product_research", description="趋势"), "")

    assert result["hits"] == ["usitc-global-digital-trade-1#583", "usitc-global-digital-trade-1#913"]
    assert [entry["doc_id"] for entry in result["citations"]] == ["usitc-global-digital-trade-1"]  # 被引池只有它
    assert result["evidence"] is None  # 检索类命中不进证据块(#69 收口面不动)


async def test_supervisor_carries_retrieval_hit_ids_into_results_and_batch_row() -> None:
    """命中池随切片产出下发(get_task 的 results),并进批次行(durable 重放不丢这份数据)。"""

    async def run(slice_: Slice, _task_request: str) -> dict:
        return {
            "agent": slice_.agent,
            "description": slice_.description,
            "executed": True,
            "actions": [APPROVE_ACTION],
            "hits": ["usitc-global-digital-trade-1#913"],
        }

    store = InMemoryApprovalBatchStore()
    graph = build_supervisor(
        _SinglePlan(),
        agents={"order_management": run},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=FakeApply(),
    )
    config = {"configurable": {"thread_id": "hit-ids"}}

    await graph.ainvoke(SupervisorState(request="查订单", thread_id=config["configurable"]["thread_id"]), config)
    await _approve_pending(graph, config)

    final = await graph.aget_state(config)
    assert final.values["results"][1]["hits"] == ["usitc-global-digital-trade-1#913"]
    assert store.batches[0].run_output is not None
    assert store.batches[0].run_output["hits"] == ["usitc-global-digital-trade-1#913"]
