"""业务子图测试(spec #7 seam 1):工具暴露(B6)、执行行为(B7 收集/免审直行)、
客服查证优先边约束(B12)、步数上限(B17)。全部不依赖真实 LLM/DB。"""

from __future__ import annotations

import json

from python_backend.agents.base import AgentState, build_react_agent
from python_backend.agents.customer_service.agent import build_customer_agent
from python_backend.agents.executor import ToolExecutor, action_of
from python_backend.agents.order_management.agent import build_order_agent
from python_backend.agents.product_research.agent import build_product_agent
from python_backend.core.approvals import classify_action
from python_backend.domain.tools import ToolDefinition, ToolRegistry
from python_backend.infrastructure.llm import ToolCallResult
from python_backend.vector_repo.base import VectorRecord
from tests.conftest import FakeEmbedding, FakeExecutor, FakeLlm, InMemoryVectorRepository


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


def round_thinking(content: str, reasoning: str) -> ToolCallResult:
    """思考轮:正文与 reasoning 分开给(#64 B1 的实测形状:正文整体落在 reasoning 里)。"""
    return ToolCallResult(content=content, tool_calls=[], reasoning_content=reasoning)


async def run(graph, description: str = "测试切片", task_request: str = "") -> dict:
    return await graph.ainvoke(AgentState(slice_description=description, task_request=task_request))


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


# —— B30③:作答轮正文为空(空正文不算作答,#64 B1)——


async def test_react_blank_answer_retries_once_then_incomplete() -> None:
    """作答轮空正文 → 同预算重试一次;仍空即判「未完成(正文为空)」,不落一个空的 answer。"""
    llm = FakeLlm(tool_rounds=[round_thinking("", reasoning="我把整份正文写进思考里了")])
    graph = build_react_agent(
        name="product_research",
        system_prompt="你是选品助手",
        registry=registry("trend_query"),
        executor=FakeExecutor(),
        llm=llm,
        step_limit=10,
    )
    result = await run(graph)

    assert result.get("answer") is None, "空正文不得当作答(它会在 results 里被 falsy 判掉)"
    assert result["incomplete"] is not None
    assert "正文为空" in result["incomplete"]
    assert len([c for c in llm.calls if c["method"] == "complete_with_tools"]) == 2, "重试一次即止(同预算)"


async def test_react_blank_answer_recovers_on_retry() -> None:
    """重试轮给出正文即正常收尾——护栏不该把「思考吃空一次」判成整片失败。"""
    llm = FakeLlm(
        tool_rounds=[
            round_thinking("", reasoning="先想一遍"),
            round_text("便携咖啡机在美国市场属细分品类,竞争适中。"),
        ]
    )
    graph = build_react_agent(
        name="product_research",
        system_prompt="你是选品助手",
        registry=registry("trend_query"),
        executor=FakeExecutor(),
        llm=llm,
        step_limit=10,
    )
    result = await run(graph)

    assert result["answer"] == "便携咖啡机在美国市场属细分品类,竞争适中。"
    assert result.get("incomplete") is None


async def test_react_blank_answer_retry_prompt_reaches_model() -> None:
    """重试是**再问一次**(提示词里点明正文为空),不是原地重放同一个请求。"""
    llm = FakeLlm(tool_rounds=[round_thinking("", reasoning="想"), round_text("答案")])
    graph = build_react_agent(
        name="product_research",
        system_prompt="你是选品助手",
        registry=registry("trend_query"),
        executor=FakeExecutor(),
        llm=llm,
        step_limit=10,
    )
    await run(graph)

    second_call = [c for c in llm.calls if c["method"] == "complete_with_tools"][1]
    assert any("正文" in str(message.get("content", "")) for message in second_call["messages"])


async def test_react_agent_output_budget_covers_thinking() -> None:
    """#64 裁决补:作答轮的输出预算须罩得住思考——默认 2000 被 reasoning 吃穿是空正文的根因。

    实测(新批 trace):空正文轮的 reasoning 都在 7374-7817 字,有正文的轮 ≤4008 字;而作答轮走
    `complete_with_tools` 的默认 `max_tokens=2000`(未显式传参)。同 #61 的 judge 预算一类问题,
    处置同为「放宽」——max_tokens 是上限不是预留,实际消耗不因此变大。
    """
    llm = FakeLlm(tool_rounds=[round_text("答案")])
    graph = build_react_agent(
        name="order_management",
        system_prompt="你是订单助手",
        registry=registry("list_orders"),
        executor=FakeExecutor(),
        llm=llm,
    )
    await run(graph)

    assert llm.calls[0]["max_tokens"] >= 8192, "预算须罩得住思考(实测思考可达 7000+ 字)"


async def test_customer_agent_output_budget_covers_thinking() -> None:
    """客服线同一口径(verify 与 draft 两个节点都走 LLM,预算不许各写一份)。"""
    llm = FakeLlm(tool_rounds=[round_text("答案")])
    graph, _ = build_customer_agent(executor=FakeExecutor(), llm=llm)
    await run(graph)

    assert llm.calls[0]["max_tokens"] >= 8192


# —— B30⑥:切片上下文补齐(原始请求随切片下发,#64 B2)——


async def test_react_slice_message_carries_global_task_and_slice_duty() -> None:
    """切片执行段的用户消息 = 全局任务 + 本切片职责两段。

    反例(2026-09-16 实录):规划器把切片 2 写成分「基于切片1数据做需求趋势与价格带分析…(漏水、
    续航、清洗难等)」——没点名品类;切片 Agent 拿不到原始请求,只能从痛点猜,猜成了「宠物饮水机」,
    整片跑偏。原请求在场即无此歧义。
    """
    llm = FakeLlm(tool_rounds=[round_text("完成")])
    graph = build_react_agent(
        name="product_research",
        system_prompt="你是选品助手",
        registry=registry("trend_query"),
        executor=FakeExecutor(),
        llm=llm,
    )
    await run(
        graph,
        description="基于切片1数据做需求趋势与价格带分析",
        task_request="分析一下便携咖啡机在美国市场的选品机会",
    )

    user_message = llm.calls[0]["messages"][1]
    assert user_message["role"] == "user"
    assert "分析一下便携咖啡机在美国市场的选品机会" in user_message["content"]
    assert "基于切片1数据做需求趋势与价格带分析" in user_message["content"]
    assert user_message["content"].index("便携咖啡机") < user_message["content"].index("基于切片1")  # 全局任务在前


async def test_slice_message_without_global_task_is_description_only() -> None:
    """无全局任务(直接驱动子图的旧调用)时退化为纯描述——不摆一个空的「全局任务」段。"""
    llm = FakeLlm(tool_rounds=[round_text("完成")])
    graph = build_react_agent(
        name="product_research",
        system_prompt="你是选品助手",
        registry=registry("trend_query"),
        executor=FakeExecutor(),
        llm=llm,
    )
    await run(graph, description="只给切片职责")

    assert llm.calls[0]["messages"][1]["content"] == "只给切片职责"


async def test_customer_slice_message_carries_global_task() -> None:
    """客服线同一条口径(结构化图有自己的消息组装处,不许各写一份)。"""
    llm = FakeLlm(tool_rounds=[round_text("完成")])
    graph, _ = build_customer_agent(executor=FakeExecutor(), llm=llm)
    await run(graph, description="处理买家消息", task_request="买家反馈商品破损要求退货")

    user_message = llm.calls[0]["messages"][1]
    assert "买家反馈商品破损要求退货" in user_message["content"]
    assert "处理买家消息" in user_message["content"]


# —— B30⑤:零命中契约(选品线不得无据出分级/报告,#64 B3)——


async def test_product_prompt_states_zero_hit_contract() -> None:
    """#64 B3:检索零命中时不得执行 scoring / generate_report / draft_create。

    依据:``coffee-maker-us#3#2`` 在有效命中 0 条时仍给出价格带与竞争度评级(还跑了 scoring),
    判「结论性论断有检索依据」失败——提示词原先只说「情报不足时如实说明」,没有正面禁止
    「无情报仍出分级与报告」这条路径。
    """
    llm = FakeLlm(tool_rounds=[round_text("完成")])
    graph, _ = build_product_agent(executor=FakeExecutor(), llm=llm)
    await run(graph)

    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert "零命中" in system_prompt
    for tool_name in ("scoring", "generate_report", "draft_create"):
        assert tool_name in system_prompt
    assert "不得执行" in system_prompt


async def test_customer_blank_draft_retries_then_incomplete() -> None:
    """B30③ 客服线终稿同护栏(draft 就是客服的作答轮):空 → 回 draft 重问一次;仍空判未完成。"""
    llm = FakeLlm(
        tool_rounds=[
            round_tools(call("faq_search", {"query": "退货"})),
            round_thinking("", reasoning="答案写在思考里"),
        ]
    )
    executor = FakeExecutor(results={"faq_search": {"hits": []}})
    graph, _ = build_customer_agent(executor=executor, llm=llm, step_limit=10)
    result = await run(graph)

    assert result.get("answer") is None
    assert result["incomplete"] is not None
    assert "正文为空" in result["incomplete"]


# —— B12:客服查证优先(图级边约束)——


async def test_customer_verify_tools_only_in_verify_node() -> None:
    _graph, reg = build_customer_agent(executor=FakeExecutor(), llm=FakeLlm())
    # issue #50:统一检索工具进客服 verify 清单(工具清单层面的既有断言随暴露面更新)
    assert [t.name for t in reg.visible_for("verify")] == [
        "faq_search",
        "knowledge_search",
        "order_lookup",
        "product_lookup",
    ]
    draft_tools = [t.name for t in reg.visible_for("draft")]
    assert "faq_search" not in draft_tools and "order_lookup" not in draft_tools
    assert "product_lookup" not in draft_tools
    assert "generate_draft" in draft_tools


async def test_unified_search_exposed_only_in_customer_domain() -> None:
    """#50 图级暴露守卫:统一检索只进客服 verify;选品域 / 订单域的工具清单不含它(ADR-0007 边界)。"""
    _, product_reg = build_product_agent(executor=FakeExecutor(), llm=FakeLlm())
    _, order_reg = build_order_agent(executor=FakeExecutor(), llm=FakeLlm())
    _, customer_reg = build_customer_agent(executor=FakeExecutor(), llm=FakeLlm())

    assert "knowledge_search" in [t.name for t in customer_reg.visible_for("verify")]
    for reg in (product_reg, order_reg):
        assert "knowledge_search" not in [t.name for t in reg.visible_for("agent")]


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


async def test_customer_unified_search_hits_both_collections_as_evidence() -> None:
    """#50:客服 verify 调用 knowledge_search —— 真实 ToolExecutor 一次查两集合,混合命中计入 B12 证据。"""
    vector = InMemoryVectorRepository()
    await vector.upsert(
        "faq", [VectorRecord(id="faq-payment#14", vector=[1.0] * 8, payload={"question": "支持哪些支付方式?"})]
    )
    await vector.upsert(
        "market_intel", [VectorRecord(id="adb-carec#46", vector=[1.0] * 8, payload={"title": "数字支付基础设施"})]
    )
    llm = FakeLlm(
        tool_rounds=[
            round_tools(call("knowledge_search", {"query": "跨境支付与退款"})),
            round_text(""),
            round_text("亲,我们支持信用卡与 PayPal 支付。"),
        ]
    )
    graph, _ = build_customer_agent(executor=ToolExecutor(vector=vector, embedding=FakeEmbedding()), llm=llm)
    result = await run(graph)

    assert result["evidence_count"] == 1
    assert result["answer"] == "亲,我们支持信用卡与 PayPal 支付。"
    observation = json.loads(next(m["content"] for m in result["messages"] if m["role"] == "tool"))
    assert {hit["id"] for hit in observation["hits"]} == {"faq-payment#14", "adb-carec#46"}


# —— B28:引用小点(issue #51) ——


def _retrieval_hit(
    chunk_id: str,
    *,
    title: str,
    section: str,
    chunk_index: int,
    content: str,
    source: str = "自造 FAQ 语料库",
    published_at: str = "2026-09-14",
) -> VectorRecord:
    """检索命中记录(形状 = 语料切块投影,带完整溯源;向量同向以便 FakeEmbedding 命中)。"""
    return VectorRecord(
        id=chunk_id,
        vector=[1.0] * 8,
        payload={
            "doc_id": chunk_id.split("#", 1)[0],
            "title": title,
            "source": source,
            "published_at": published_at,
            "section": section,
            "chunk_index": chunk_index,
            "content": content,
        },
    )


async def test_customer_answer_carries_citations_from_retrieval_hits() -> None:
    """#51:终稿据命中切块标识标注 → 归一化为上标编号;同文档合并编号,引用条目随答案出图。"""
    vector = InMemoryVectorRepository()
    await vector.upsert(
        "faq",
        [
            _retrieval_hit(
                "faq-returns#6",
                title="退款多久到账?退到哪里?",
                section="退货退款",
                chunk_index=6,
                content="Q: 退款多久到账?\nA: 仓库验收后 1-3 个工作日发起退款。",
            ),
            _retrieval_hit(
                "faq-returns#9",
                title="退款多久到账?退到哪里?",
                section="退货退款",
                chunk_index=9,
                content="Q: 旺季退款会变慢吗?\nA: 大促期间可能顺延 2-3 个工作日。",
            ),
        ],
    )
    llm = FakeLlm(
        tool_rounds=[
            round_tools(call("knowledge_search", {"query": "退款多久到账"})),
            round_text(""),
            round_text("仓库验收后 1-3 个工作日发起退款[faq-returns#6];旺季可能顺延[faq-returns#9]。"),
        ]
    )
    graph, _ = build_customer_agent(executor=ToolExecutor(vector=vector, embedding=FakeEmbedding()), llm=llm)
    result = await run(graph)

    assert result["answer"] == "仓库验收后 1-3 个工作日发起退款[1];旺季可能顺延[1]。"
    assert len(result["citations"]) == 1, "同一文档合并为一条引用"
    citation = result["citations"][0]
    assert citation["number"] == 1 and citation["doc_id"] == "faq-returns"
    assert [chunk["id"] for chunk in citation["chunks"]] == ["faq-returns#6", "faq-returns#9"]
    assert citation["chunks"][0]["section"] == "退货退款" and citation["source"] == "自造 FAQ 语料库"


async def test_customer_answer_without_retrieval_has_no_citations() -> None:
    """#51:无检索依据的产出不标引用(商品查证结果里没有 hits),文本原样出图。"""
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

    assert result["answer"] == "亲,该商品售价 129.00 元,现货充足。"
    assert result["citations"] == []


# —— B28 横切:选品域引用(issue #52) ——


async def test_product_answer_carries_citations_from_intel_hits() -> None:
    """#52:选品线答案据情报命中标注 → 归一化为上标编号;同文档合并编号,引用条目随答案出图。

    与客服线同一形状(切块标识标记 + 文档级合并),只是命中来自 trend_query / competitor_analysis。
    """
    vector = InMemoryVectorRepository()
    await vector.upsert(
        "market_intel",
        [
            _retrieval_hit(
                "usitc-global-digital-trade-1#583",
                title="Global Digital Trade 1",
                source="U.S. International Trade Commission",
                published_at="2017-08-01",
                section="pp.150-151",
                chunk_index=583,
                content="Digital trade barriers vary by market.",
            ),
            _retrieval_hit(
                "usitc-global-digital-trade-1#590",
                title="Global Digital Trade 1",
                source="U.S. International Trade Commission",
                published_at="2017-08-01",
                section="pp.152-153",
                chunk_index=590,
                content="Cross-border data flows remain restricted.",
            ),
        ],
    )
    llm = FakeLlm(
        tool_rounds=[
            round_tools(call("trend_query", {"query": "跨境数字贸易 趋势"})),
            round_tools(call("competitor_analysis", {"query": "跨境数字贸易 竞品"}, "call_2")),
            round_text(
                "跨境数据流动仍受限[usitc-global-digital-trade-1#590];"
                "壁垒因市场而异[usitc-global-digital-trade-1#583]。"
            ),
        ]
    )
    executor = ToolExecutor(vector=vector, embedding=FakeEmbedding())
    graph, _registry = build_product_agent(executor=executor, llm=llm)

    final = await run(graph, "分析跨境数字贸易市场并生成选品报告")

    assert final["answer"] == "跨境数据流动仍受限[1];壁垒因市场而异[1]。"
    assert len(final["citations"]) == 1, "同一文档合并为一条引用"
    citation = final["citations"][0]
    assert citation["number"] == 1 and citation["doc_id"] == "usitc-global-digital-trade-1"
    assert citation["title"] == "Global Digital Trade 1"
    assert citation["source"] == "U.S. International Trade Commission"
    assert [chunk["id"] for chunk in citation["chunks"]] == [
        "usitc-global-digital-trade-1#590",  # 顺序 = 文本内被引出现的顺序
        "usitc-global-digital-trade-1#583",
    ]
    assert citation["chunks"][0]["score"] == 1.0 and citation["chunks"][0]["section"] == "pp.152-153"


async def test_product_answer_without_retrieval_has_no_citations() -> None:
    """#52:无情报命中的选品答案(如仅评分)文本原样、引用为空;序号标记不被静默锚定(#51 同口径)。"""
    llm = FakeLlm(
        tool_rounds=[
            round_tools(call("scoring", {"product_title": "蓝牙音箱"})),
            round_text("评分 88 分(A 级),列第 1 候选[1]。"),
        ]
    )
    executor = FakeExecutor(results={"scoring": {"score": 88, "grade": "A"}})
    graph, _ = build_product_agent(executor=executor, llm=llm)

    final = await run(graph, "给蓝牙音箱打分")

    assert final["answer"] == "评分 88 分(A 级),列第 1 候选[1]。"
    assert final["citations"] == []


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


# —— A3 选品四步串联(issue #44) ——


class _SelectionLlm(FakeLlm):
    """选品链专用假 LLM:按 json_mode 路由 complete 响应。

    scoring(json_mode=True)→ 评分 JSON;generate_report → 报告正文。
    (FakeLlm.complete 按全局调用序取响应,而本链中工具轮也计入调用序——
    按语义路由比按序号索引可读且不随轮数漂移;工具轮仍走 FakeLlm 脚本。)
    """

    async def complete(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        json_mode: bool = False,
    ) -> str:
        self.calls.append(
            {"method": "complete", "messages": messages, "json_mode": json_mode, "max_tokens": max_tokens}
        )
        if json_mode:
            return json.dumps({"score": 88, "grade": "A", "rationale": "需求旺盛"})
        return "# 选品报告\n\n评分等级 A,建议上架。"


async def test_product_agent_runs_four_step_selection_pipeline() -> None:
    """A3 串联证据:脚本化 ReAct 轮驱动**真实 ToolExecutor**——trend_query → competitor_analysis
    → scoring → generate_report 四步依序走通,评分等级先于报告步进入对话。

    「报告正文含评分等级」的 LLM 行为面属在线走查(演示)职责,离线不伪证;
    此处证的是工具链形状与顺序(四步观察按序回喂、等级入上下文)。
    """
    vector = InMemoryVectorRepository()
    await vector.upsert(
        "market_intel",
        [
            VectorRecord(id="trend-1", vector=[1.0] * 8, payload={"title": "宠物饮水机需求上行"}),
            VectorRecord(id="comp-1", vector=[1.0] * 8, payload={"title": "竞品价格带 15-30 美元"}),
        ],
    )
    llm = _SelectionLlm(
        tool_rounds=[
            round_tools(call("trend_query", {"query": "宠物饮水机 趋势"}, "c1")),
            round_tools(call("competitor_analysis", {"query": "宠物饮水机 竞品"}, "c2")),
            round_tools(call("scoring", {"product_title": "宠物饮水机"}, "c3")),
            round_tools(call("generate_report", {"context": "趋势 + 竞品 + 评分(A)汇总"}, "c4")),
            round_text("四步走完,报告已生成。"),
        ],
    )
    executor = ToolExecutor(llm=llm, vector=vector, embedding=FakeEmbedding())
    graph, _registry = build_product_agent(executor=executor, llm=llm)

    final = await run(graph, "分析宠物饮水机是否值得做")

    observations = [m["content"] for m in final["messages"] if m["role"] == "tool"]
    assert len(observations) == 4, "四步各产生一条工具观察(依序回喂)"
    assert "需求上行" in observations[0], "第一步:趋势情报"
    assert "竞品价格带" in observations[1], "第二步:竞品情报"
    assert '"grade": "A"' in observations[2], "第三步:评分等级先于报告步进入对话"
    assert "选品报告" in observations[3], "第四步:报告产出"
    assert final["answer"] == "四步走完,报告已生成。"
    assert final.get("incomplete") is None
