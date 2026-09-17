"""工具执行器测试(spec #7 seam):auto 直行、approval 捕获现状快照、apply 漂移冲突与并发串行化(B7/B18)。

单元部分(不依赖外部服务):LLM 工具(FakeLlm)、向量工具(内存仓库 + 假 embedding)、纯函数工具。
integration 部分(真 PG):DB 工具落库、capture 快照、apply 事务语义(批内同进同退/漂移/并发)。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from python_backend.agents.executor import ApplyResult, ToolExecutor, apply_batch_actions
from python_backend.core.approvals import classify_action
from python_backend.db.models import Order, OrderStatus, Product, ProductStatus, Ticket
from python_backend.db.session import SessionFactory
from python_backend.vector_repo.base import VectorRecord
from tests.conftest import (
    FakeEmbedding,
    FakeLlm,
    InMemoryProductLookup,
    InMemoryVectorRepository,
    require_postgres,
)


def make_executor(llm: FakeLlm | None = None) -> ToolExecutor:
    vector = InMemoryVectorRepository()
    return ToolExecutor(llm=llm, vector=vector, embedding=FakeEmbedding())


# —— 单元:分类表扩展(spec #7:auto 动作全部进免审层,审批/禁做语义不变)——


def test_classify_action_covers_all_exposed_tools() -> None:
    """新增 auto 工具动作全部为 auto;审批动作不变;未知默认 forbidden。"""
    auto_actions = [
        "trend_query",
        "competitor_analysis",
        "scoring",
        "generate_report",
        "draft.create",
        "draft.edit",
        "list_orders",
        "product_lookup",
        "check_inventory",
        "detect_anomalies",
        "list_approvals",
        "faq_search",
        "knowledge_search",
        "order_lookup",
        "translate",
        "sentiment_analysis",
        "manage_template",
        "escalate_ticket",
        "generate_draft",
    ]
    for action in auto_actions:
        assert classify_action(action) == "auto", action
    assert classify_action("product.publish") == "approval"
    assert classify_action("order.transition") == "approval"
    assert classify_action("product.hack_price") == "forbidden"


# —— 单元:纯函数工具(无 DB/LLM)——


async def test_execute_detect_anomalies_keyword_scan() -> None:
    result = await make_executor().execute("detect_anomalies", {"description": "商品破损,客户要求退款"})
    assert result["anomaly"] is True
    assert "破损" in result["reason"]


async def test_execute_unknown_action_raises() -> None:
    with pytest.raises(ValueError, match="未知动作"):
        await make_executor().execute("no_such_tool", {})


# —— 单元:LLM 工具 ——


async def test_execute_translate_via_llm() -> None:
    llm = FakeLlm(responses=["Hello, this is a nice day."])
    result = await make_executor(llm).execute("translate", {"text": "今天天气不错", "target_locale": "en"})
    assert result["translated"] == "Hello, this is a nice day."
    assert llm.calls[0]["json_mode"] is False


async def test_execute_scoring_parses_json() -> None:
    llm = FakeLlm(responses=[json.dumps({"score": 88, "grade": "A", "rationale": "需求旺盛"})])
    result = await make_executor(llm).execute("scoring", {"product_title": "宠物饮水机"})
    assert result["score"] == 88
    assert result["grade"] == "A"


@pytest.mark.parametrize(
    ("action", "params", "response", "floor"),
    [
        ("scoring", {"product_title": "宠物饮水机"}, '{"score": 88, "grade": "A", "rationale": "需求旺盛"}', 16384),
        ("translate", {"text": "你好", "target_locale": "英语"}, "hello", 16384),
        ("sentiment_analysis", {"text": "物流太慢了"}, "negative", 16384),
        ("generate_draft", {"buyer_message": "还有货吗?", "evidence": "库存 2"}, "亲,还有货。", 16384),
        ("generate_report", {"context": "情报与评分汇总"}, "# 选品报告", 8000),
    ],
    ids=["scoring", "translate", "sentiment", "draft", "report"],
)
async def test_llm_completion_budget_leaves_reasoning_headroom(
    action: str, params: dict, response: str, floor: int
) -> None:
    """走查缺陷:思考模式(v4 flash)推理与正文共享 max_tokens——实测短任务推理 700~1200 字符、
    报告类推理 3200~5700 字符且正文 2500~3500 字符;预算被推理耗尽即空正文(finish_reason=length),
    各调用点须留足推理余量(下限见 floor,禁止回退到饿死档)。

    下限档位分两种(#68 裁决):会产出用户可见正文的调用点统一 ``AGENT_MAX_TOKENS``(含
    generate_draft——原 800 是全仓最低档);报告类已在 8000 档且有自身实测依据,本轮未动。
    """
    llm = FakeLlm(responses=[response])
    await make_executor(llm).execute(action, params)
    assert llm.calls[0]["max_tokens"] >= floor


async def test_execute_generate_report_and_draft() -> None:
    llm = FakeLlm(responses=["# 选品报告\\n\\n结论:值得做。", "买家您好,已为您查询。"])
    executor = make_executor(llm)
    report = await executor.execute("generate_report", {"context": "宠物市场"})
    assert report["report"].startswith("# 选品报告")
    draft = await executor.execute("generate_draft", {"buyer_message": "我的订单到哪了?", "evidence": "已发货"})
    assert draft["draft"] == "买家您好,已为您查询。"


async def test_tool_llm_call_retries_once_on_blank_content() -> None:
    """#68:工具内的 LLM 调用同吃「空正文同预算重试一次」——第一次饿空、第二次拿到正文即成功。

    依据:空正文是可恢复的采样抖动(#65 立的护栏,`llm.complete_with_blank_retry`);工具侧原先
    只有「失败转诚实观察」,代价是烧 ReAct 步数且依赖模型自发再调一次工具。
    """
    from python_backend.infrastructure.llm import AGENT_MAX_TOKENS, LlmEmptyContent

    llm = FakeLlm(responses=[LlmEmptyContent("LLM 返回空内容(finish_reason=length)"), "hello"])

    result = await make_executor(llm).execute("translate", {"text": "你好", "target_locale": "英语"})

    assert result["translated"] == "hello"
    assert [call["max_tokens"] for call in llm.calls] == [AGENT_MAX_TOKENS, AGENT_MAX_TOKENS]


async def test_execute_sentiment_analysis() -> None:
    llm = FakeLlm(responses=["negative"])
    result = await make_executor(llm).execute("sentiment_analysis", {"text": "物流太慢了,差评"})
    assert result["sentiment"] == "negative"


# —— 单元:向量工具(内存仓库 + 假 embedding)——


async def test_execute_faq_search_returns_hits() -> None:
    vector = InMemoryVectorRepository()
    await vector.upsert(
        "faq",
        [VectorRecord(id="f1", vector=[1.0] * 8, payload={"question": "如何退货?", "answer": "7 天内可退"})],
    )
    executor = ToolExecutor(vector=vector, embedding=FakeEmbedding())
    result = await executor.execute("faq_search", {"query": "退货"})
    assert len(result["hits"]) == 1
    assert result["hits"][0]["payload"]["question"] == "如何退货?"


async def test_execute_trend_query_empty_collection_returns_empty() -> None:
    result = await make_executor().execute("trend_query", {"query": "宠物用品"})
    assert result["hits"] == []


async def test_execute_competitor_analysis_returns_market_intel_hits() -> None:
    """A3 第二步功能用例(issue #44):竞品分析走 market_intel 检索,情报条目入 hits。

    此前 competitor_analysis 仅出现在分类覆盖清单,无功能用例;与 trend_query 同集合、同形状。
    """
    vector = InMemoryVectorRepository()
    await vector.upsert(
        "market_intel",
        [
            VectorRecord(
                id="m1",
                vector=[1.0] * 8,
                payload={"title": "宠物饮水机竞品价格带", "summary": "主流 15-30 美元"},
            )
        ],
    )
    executor = ToolExecutor(vector=vector, embedding=FakeEmbedding())
    result = await executor.execute("competitor_analysis", {"query": "宠物饮水机 竞品"})
    assert [hit["id"] for hit in result["hits"]] == ["m1"]
    assert "竞品价格带" in result["hits"][0]["payload"]["title"]


# —— 单元:统一检索工具(issue #50:一次查 faq + market_intel 两集合) ——


class _FixedEmbedding:
    """固定查询向量(仅测接线):记录向量决定得分,让合并后的顺序可断言。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


async def test_execute_knowledge_search_merges_both_collections_by_score() -> None:
    """#50:一次查询同时命中两集合,合并后按 score 降序;id 在 hit 层(不在 payload 内)。"""
    vector = InMemoryVectorRepository()
    await vector.upsert(
        "faq", [VectorRecord(id="faq-returns#6", vector=[0.6, 0.8], payload={"question": "退款多久到账?"})]
    )
    await vector.upsert(
        "market_intel", [VectorRecord(id="adb-carec#46", vector=[1.0, 0.0], payload={"title": "区域电商趋势"})]
    )
    result = await ToolExecutor(vector=vector, embedding=_FixedEmbedding()).execute(
        "knowledge_search", {"query": "退款与区域电商趋势"}
    )

    assert [hit["id"] for hit in result["hits"]] == ["adb-carec#46", "faq-returns#6"]  # 两集合混合 + 降序
    assert set(result["hits"][0]) == {"id", "score", "payload"}  # id 在 hit 层:#51 的引用标记锚它
    assert result["hits"][0]["payload"]["title"] == "区域电商趋势"


async def test_execute_knowledge_search_dedups_same_id_keeping_higher_score() -> None:
    """合并去重:同 id 出现在两集合时只留一条,取高分那条(cosine 1.0 > 0.707)。"""
    vector = InMemoryVectorRepository()
    await vector.upsert("faq", [VectorRecord(id="dup-1", vector=[1.0, 0.0], payload={"from": "faq"})])
    await vector.upsert("market_intel", [VectorRecord(id="dup-1", vector=[1.0, 1.0], payload={"from": "intel"})])
    result = await ToolExecutor(vector=vector, embedding=_FixedEmbedding()).execute(
        "knowledge_search", {"query": "重复 id"}
    )

    assert len(result["hits"]) == 1
    assert result["hits"][0]["payload"] == {"from": "faq"}


# —— 单元:product_lookup(issue #35;注入替身直查 executor.execute 真工具链)——


def _lookup_fixture(*, count: int) -> list[Product]:
    """内存商品样本:1 条固定 SKU + N 条同前缀标题(测截断用),主键显式给定(不入库)。"""
    rows = [
        Product(
            id=1,
            sku="DEMO-OD-002",
            title="防水手机壳",
            price=Decimal("9.99"),
            currency="USD",
            category="配件",
            status=ProductStatus.ACTIVE,
            stock=3,
        )
    ]
    for index in range(count):
        rows.append(
            Product(
                id=100 + index,
                sku=f"DEMO-OD-{1000 + index}",
                title=f"宠物饮水机 {index:02d}",
                price=Decimal("19.90"),
                currency="USD",
                category="宠物",
                status=ProductStatus.DRAFT,
                stock=5,
            )
        )
    return rows


def _lookup_executor(rows: list[Product]) -> ToolExecutor:
    return ToolExecutor(
        llm=FakeLlm(),
        vector=InMemoryVectorRepository(),
        embedding=FakeEmbedding(),
        products=InMemoryProductLookup(rows),
    )


async def test_execute_product_lookup_by_sku() -> None:
    """SKU 精确命中(大小写不敏感),返回定位所需字段。"""
    executor = _lookup_executor(_lookup_fixture(count=0))
    result = await executor.execute("product_lookup", {"sku": "demo-od-002"})
    assert result["truncated"] is False
    assert result["matches"] == [
        {
            "id": 1,
            "sku": "DEMO-OD-002",
            "title": "防水手机壳",
            "price": "9.99",
            "currency": "USD",
            "category": "配件",
            "status": "active",
            "stock": 3,
        }
    ]


async def test_execute_product_lookup_title_truncates_at_limit() -> None:
    """标题模糊命中超上限:回前 10 条并如实标注 truncated(不静默截断)。"""
    executor = _lookup_executor(_lookup_fixture(count=12))
    result = await executor.execute("product_lookup", {"title": "饮水机"})
    assert len(result["matches"]) == 10
    assert result["truncated"] is True
    assert all("饮水机" in match["title"] for match in result["matches"])


async def test_execute_product_lookup_no_match_returns_empty() -> None:
    executor = _lookup_executor(_lookup_fixture(count=0))
    assert await executor.execute("product_lookup", {"sku": "NOT-EXIST"}) == {"matches": [], "truncated": False}
    assert (await executor.execute("product_lookup", {"title": "饮水机"}))["matches"] == []


async def test_execute_product_lookup_requires_a_param() -> None:
    """三个入参至少给一:均缺显式报错,不猜(issue #42 起含 category)。"""
    executor = _lookup_executor(_lookup_fixture(count=0))
    with pytest.raises(ValueError, match="至少其一"):
        await executor.execute("product_lookup", {})


async def test_execute_product_lookup_by_category() -> None:
    """issue #42(A5「按类目查询」):类目精确匹配(大小写不敏感),支撑「按类目盘货」问法。"""
    executor = _lookup_executor(_lookup_fixture(count=2))
    result = await executor.execute("product_lookup", {"category": "宠物"})
    assert [match["id"] for match in result["matches"]] == [100, 101]
    assert result["truncated"] is False

    latin = [
        Product(
            id=7,
            sku="X-1",
            title="Warranty Card",
            price=Decimal("1.00"),
            currency="USD",
            category="Accessories",
            status=ProductStatus.ACTIVE,
            stock=1,
        )
    ]
    matched = await _lookup_executor(latin).execute("product_lookup", {"category": "accessories"})
    assert [match["id"] for match in matched["matches"]] == [7], "大小写不敏感"
    no_fuzzy = await executor.execute("product_lookup", {"category": "宠物类目"})
    assert no_fuzzy["matches"] == [], "类目为精确匹配,不做模糊"


async def test_execute_product_lookup_sku_takes_precedence_over_title_and_category() -> None:
    """同给多参以 sku 为准(精确 > 模糊 > 枚举):标题/类目指向别处也不影响 SKU 定位。"""
    executor = _lookup_executor(_lookup_fixture(count=1))
    result = await executor.execute("product_lookup", {"sku": "DEMO-OD-002", "title": "饮水机", "category": "宠物"})
    assert [match["id"] for match in result["matches"]] == [1]
    by_title = await executor.execute("product_lookup", {"title": "饮水机", "category": "配件"})
    assert [match["id"] for match in by_title["matches"]] == [100], "标题优先于类目"


# —— integration:DB 工具(capture/apply 见下)——


@pytest.fixture
async def product() -> Product:
    require_postgres()
    async with SessionFactory() as session:
        row = Product(sku=f"SKU-{uuid.uuid4().hex[:8]}", title="宠物饮水机", price=Decimal("9.99"), category="宠物")
        session.add(row)
        await session.commit()
        return row


async def test_execute_draft_create_inserts_draft(product: Product) -> None:
    require_postgres()
    result = await make_executor().execute(
        "draft.create",
        {"sku": f"SKU-{uuid.uuid4().hex[:8]}", "title": "自动饮水机", "price": "19.9", "category": "宠物"},
    )
    assert result["status"] == "draft"
    async with SessionFactory() as session:
        row = await session.get(Product, int(result["product_id"]))
        assert row is not None and row.status.value == "draft"


async def test_execute_draft_create_duplicate_sku_raises(product: Product) -> None:
    require_postgres()
    with pytest.raises(ValueError, match="已存在"):
        await make_executor().execute(
            "draft.create", {"sku": product.sku, "title": "重复", "price": "1.0", "category": "宠物"}
        )


async def test_execute_draft_edit_updates_only_draft(product: Product) -> None:
    require_postgres()
    executor = make_executor()
    result = await executor.execute("draft.edit", {"product_id": product.id, "title": "新版标题", "price": "12.5"})
    assert result["title"] == "新版标题"
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None and row.price == Decimal("12.50")


async def test_execute_draft_edit_non_draft_raises(product: Product) -> None:
    require_postgres()
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None
        row.status = ProductStatus.ACTIVE
        await session.commit()
    with pytest.raises(ValueError, match="仅草稿"):
        await make_executor().execute("draft.edit", {"product_id": product.id, "title": "非法编辑"})


async def test_execute_draft_create_with_alert_threshold(product: Product) -> None:
    """spec #9:草稿创建可带库存告警阈值(缺省取默认 10)。"""
    require_postgres()
    executor = make_executor()
    with_threshold = await executor.execute(
        "draft.create",
        {
            "sku": f"SKU-{uuid.uuid4().hex[:8]}",
            "title": "带阈值草稿",
            "price": "19.9",
            "category": "宠物",
            "alert_threshold": 3,
        },
    )
    default_threshold = await executor.execute(
        "draft.create",
        {"sku": f"SKU-{uuid.uuid4().hex[:8]}", "title": "缺省阈值草稿", "price": "9.9", "category": "宠物"},
    )
    async with SessionFactory() as session:
        first = await session.get(Product, int(with_threshold["product_id"]))
        second = await session.get(Product, int(default_threshold["product_id"]))
        assert first is not None and first.alert_threshold == 3
        assert second is not None and second.alert_threshold == 10


async def test_execute_draft_create_invalid_alert_threshold_raises(product: Product) -> None:
    """非法阈值(0/负数/非整数)如实报错,不静默替换。"""
    require_postgres()
    for bad in (0, -1, "abc"):
        with pytest.raises(ValueError, match="告警阈值"):
            await make_executor().execute(
                "draft.create",
                {
                    "sku": f"SKU-{uuid.uuid4().hex[:8]}",
                    "title": "坏阈值",
                    "price": "1.0",
                    "category": "宠物",
                    "alert_threshold": bad,
                },
            )


async def test_execute_draft_edit_updates_alert_threshold(product: Product) -> None:
    """spec #9:草稿编辑可改库存告警阈值(免审,草稿内部编辑)。"""
    require_postgres()
    result = await make_executor().execute("draft.edit", {"product_id": product.id, "alert_threshold": 7})
    assert result["alert_threshold"] == 7
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None and row.alert_threshold == 7


async def test_execute_check_inventory_reads_real_stock(product: Product) -> None:
    """A7:库存检查读库(不再 LLM 自报),五档告警文案。"""
    require_postgres()
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None
        row.stock = 2
        await session.commit()
    executor = make_executor()
    result = await executor.execute("check_inventory", {"product_id": product.id, "threshold": 5})
    assert result["current_stock"] == 2
    assert result["alert"] is True
    assert "偏低" in result["message"]


async def test_execute_check_inventory_five_tiers(product: Product) -> None:
    require_postgres()
    executor = make_executor()
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None
        for stock, expected in [(0, "售罄"), (1, "严重不足"), (2, "偏低"), (4, "接近安全线"), (6, "充足")]:
            row.stock = stock
            await session.commit()
            result = await executor.execute("check_inventory", {"product_id": product.id, "threshold": 5})
            assert expected in result["message"], (stock, result["message"])


async def test_execute_check_inventory_uses_product_threshold_by_default(product: Product) -> None:
    """A9:阈值参数缺省 → 读商品自身 alert_threshold(消除 LLM 自报阈值残余)。"""
    require_postgres()
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None
        row.stock = 2
        row.alert_threshold = 8
        await session.commit()
    executor = make_executor()
    result = await executor.execute("check_inventory", {"product_id": product.id})
    assert result["threshold"] == 8
    assert result["alert"] is True
    assert "安全线: 8" in result["message"]


async def test_execute_list_orders_and_lookup(product: Product) -> None:
    require_postgres()
    async with SessionFactory() as session:
        order = Order(product_id=product.id, status=OrderStatus.PENDING, total_amount=Decimal("19.98"), currency="USD")
        session.add(order)
        await session.commit()
        order_id = order.id
    executor = make_executor()
    orders = await executor.execute("list_orders", {})
    mine = [o for o in orders if o["product_id"] == product.id]
    assert len(mine) == 1
    assert mine[0]["status"] == "pending"
    lookup = await executor.execute("order_lookup", {"order_id": order_id})
    assert lookup["id"] == order_id


async def test_execute_escalate_ticket_inserts_ticket() -> None:
    require_postgres()
    result = await make_executor().execute("escalate_ticket", {"message": "客户要求升级处理"})
    async with SessionFactory() as session:
        rows = (await session.execute(select(Ticket))).scalars().all()
        assert any(row.id == result["ticket_id"] and row.message == "客户要求升级处理" for row in rows)


async def test_execute_manage_template_crud() -> None:
    require_postgres()
    scenario = f"shipping_delay_{uuid.uuid4().hex[:6]}"
    executor = make_executor()
    created = await executor.execute(
        "manage_template",
        {"action": "create", "scenario": scenario, "locale": "en", "template": "Sorry for the delay."},
    )
    got = await executor.execute("manage_template", {"action": "get", "scenario": scenario, "locale": "en"})
    assert got["template"] == "Sorry for the delay."
    updated = await executor.execute(
        "manage_template",
        {"action": "update", "scenario": scenario, "locale": "en", "template": "Updated template."},
    )
    assert updated["template"] == "Updated template."
    assert created["id"]


async def test_execute_list_approvals_reads_batches(product: Product) -> None:
    require_postgres()
    from python_backend.db.approval_store import PostgresApprovalBatchStore

    store = PostgresApprovalBatchStore()
    await store.create_batch(
        batch_id=str(uuid.uuid4()),
        thread_id="t-list",
        slice_no=1,
        action_type="product.publish",
        actions=[{"action": "product.publish", "params": {"product_id": product.id}}],
        mode="approval",
    )
    result = await make_executor().execute("list_approvals", {"status": "pending"})
    assert any(row["action_type"] == "product.publish" for row in result)


# —— integration:product_lookup 真 PG 实现(issue #35)——


async def test_postgres_product_lookup_case_insensitive_and_scoped(product: Product) -> None:
    """PG 实现经工具链直查:SKU 大小写不敏感精确(不误命中同前缀 SKU)。"""
    require_postgres()
    executor = make_executor()
    result = await executor.execute("product_lookup", {"sku": product.sku.lower()})
    assert [match["id"] for match in result["matches"]] == [product.id]
    assert (await executor.execute("product_lookup", {"sku": f"{product.sku}-x"}))["matches"] == []


async def test_postgres_product_lookup_title_fuzzy(product: Product) -> None:
    """标题 contains 直查 PG;搜索串取唯一随机后缀(库内多行同标题时模糊命中被上限截断)。"""
    require_postgres()
    token = uuid.uuid4().hex[:8]
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None
        row.title = f"限定探针商品 {token}"
        await session.commit()
    result = await make_executor().execute("product_lookup", {"title": f"限定探针商品 {token}"})
    assert [match["id"] for match in result["matches"]] == [product.id]


async def test_postgres_product_lookup_by_category(product: Product) -> None:
    """issue #42:类目大小写不敏感精确直查 PG;探针类目为唯一值(库内同类目多商品会按上限截断)。"""
    require_postgres()
    token = uuid.uuid4().hex[:8]
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None
        row.category = f"Probe-{token}"
        await session.commit()
    result = await make_executor().execute("product_lookup", {"category": f"probe-{token}"})
    assert [match["id"] for match in result["matches"]] == [product.id]
    assert (await make_executor().execute("product_lookup", {"category": f"Probe-{token}-x"}))["matches"] == []


# —— integration:capture 现状快照 ——


async def test_capture_product_snapshot(product: Product) -> None:
    require_postgres()
    snapshot = await make_executor().capture("product.publish", {"product_id": product.id})
    assert snapshot == {"exists": True, "status": "draft", "title": "宠物饮水机", "sku": product.sku}


async def test_capture_missing_product_marks_not_exists() -> None:
    require_postgres()
    snapshot = await make_executor().capture("product.publish", {"product_id": 999999})
    assert snapshot == {"exists": False}


async def test_capture_order_transition_snapshot(product: Product) -> None:
    require_postgres()
    async with SessionFactory() as session:
        order = Order(product_id=product.id, status=OrderStatus.PENDING, total_amount=Decimal("9.99"))
        session.add(order)
        await session.commit()
        snapshot = await make_executor().capture("order.transition", {"order_id": order.id})
        assert snapshot["status"] == "pending"


async def test_capture_unknown_action_raises() -> None:
    require_postgres()
    with pytest.raises(ValueError, match="无快照"):
        await make_executor().capture("detect_anomalies", {})


# —— integration:apply 事务语义(批内同进同退/漂移/并发,B18)——


async def _approved_batch(action_type: str, actions: list[dict]) -> str:
    """创建并批准一个批次(真 PG,镜像生产流程),返回 batch_id。"""
    from python_backend.db.approval_store import PostgresApprovalBatchStore

    store = PostgresApprovalBatchStore()
    batch_id = str(uuid.uuid4())
    await store.create_batch(
        batch_id=batch_id,
        thread_id=f"t-{batch_id[:8]}",
        slice_no=1,
        action_type=action_type,
        actions=actions,
        mode="approval",
    )
    await store.decide_batch(batch_id=batch_id, decision="approve")
    return batch_id


async def test_apply_publish_happy_path(product: Product) -> None:
    require_postgres()
    snapshot = await make_executor().capture("product.publish", {"product_id": product.id})
    actions = [{"action": "product.publish", "params": {"product_id": product.id}, "snapshot": snapshot}]
    result = await apply_batch_actions(await _approved_batch("product.publish", actions), actions)
    assert result.applied is True
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None and row.status.value == "active"


async def test_apply_update_price_happy_path(product: Product) -> None:
    require_postgres()
    snapshot = await make_executor().capture("product.update_price", {"product_id": product.id})
    actions = [
        {
            "action": "product.update_price",
            "params": {"product_id": product.id, "new_price": "29.9"},
            "snapshot": snapshot,
        }
    ]
    result = await apply_batch_actions(await _approved_batch("product.update_price", actions), actions)
    assert result.applied is True
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None and row.price == Decimal("29.90")


async def test_apply_drift_conflict_rolls_back_whole_batch(product: Product) -> None:
    """批内同进同退:一个动作漂移,整批回滚(状态与价格都不变)。"""
    require_postgres()
    publish_snapshot = await make_executor().capture("product.publish", {"product_id": product.id})
    price_snapshot = await make_executor().capture("product.update_price", {"product_id": product.id})
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None
        row.price = Decimal("99.00")  # 模拟他处改动:价格快照漂移
        await session.commit()

    actions = [
        {"action": "product.publish", "params": {"product_id": product.id}, "snapshot": publish_snapshot},
        {
            "action": "product.update_price",
            "params": {"product_id": product.id, "new_price": "29.9"},
            "snapshot": price_snapshot,
        },
    ]
    result = await apply_batch_actions(await _approved_batch("product.publish", actions), actions)

    assert result.applied is False
    assert "价格" in (result.reason or "")
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None and row.status.value == "draft"  # publish 也被回滚
        assert row.price == Decimal("99.00")  # 他处改动未被覆盖


async def test_apply_transition_valid(product: Product) -> None:
    require_postgres()
    async with SessionFactory() as session:
        order = Order(product_id=product.id, status=OrderStatus.PENDING, total_amount=Decimal("9.99"))
        session.add(order)
        await session.commit()
        order_id = order.id
    snapshot = await make_executor().capture("order.transition", {"order_id": order_id})
    actions = [
        {
            "action": "order.transition",
            "params": {"order_id": order_id, "to_status": "confirmed"},
            "snapshot": snapshot,
        }
    ]
    result = await apply_batch_actions(await _approved_batch("order.transition", actions), actions)
    assert result.applied is True
    # 效果描述(spec #9):提交后供调用方组装通知
    assert result.effects == [{"type": "order_status", "order_id": order_id, "from": "pending", "to": "confirmed"}]
    async with SessionFactory() as session:
        row = await session.get(Order, order_id)
        assert row is not None and row.status == OrderStatus.CONFIRMED


async def test_apply_transition_illegal_move_is_conflict(product: Product) -> None:
    """非法流转(状态机外)apply 时拒绝。"""
    require_postgres()
    async with SessionFactory() as session:
        order = Order(product_id=product.id, status=OrderStatus.PENDING, total_amount=Decimal("9.99"))
        session.add(order)
        await session.commit()
        order_id = order.id
    snapshot = await make_executor().capture("order.transition", {"order_id": order_id})
    actions = [
        {
            "action": "order.transition",
            "params": {"order_id": order_id, "to_status": "delivered"},
            "snapshot": snapshot,
        }
    ]
    result = await apply_batch_actions(await _approved_batch("order.transition", actions), actions)
    assert result.applied is False
    assert "不可" in (result.reason or "")


async def test_apply_transition_drift_conflict(product: Product) -> None:
    require_postgres()
    async with SessionFactory() as session:
        order = Order(product_id=product.id, status=OrderStatus.PENDING, total_amount=Decimal("9.99"))
        session.add(order)
        await session.commit()
        order_id = order.id
    snapshot = await make_executor().capture("order.transition", {"order_id": order_id})
    async with SessionFactory() as session:
        order = await session.get(Order, order_id)
        assert order is not None
        order.status = OrderStatus.CANCELLED  # 他处先取消了
        await session.commit()

    actions = [
        {
            "action": "order.transition",
            "params": {"order_id": order_id, "to_status": "confirmed"},
            "snapshot": snapshot,
        }
    ]
    result = await apply_batch_actions(await _approved_batch("order.transition", actions), actions)
    assert result.applied is False
    assert "状态已变化" in (result.reason or "")


async def test_apply_cancel_from_pending(product: Product) -> None:
    require_postgres()
    async with SessionFactory() as session:
        order = Order(product_id=product.id, status=OrderStatus.PENDING, total_amount=Decimal("9.99"))
        session.add(order)
        await session.commit()
        order_id = order.id
    snapshot = await make_executor().capture("order.cancel", {"order_id": order_id})
    actions = [{"action": "order.cancel", "params": {"order_id": order_id}, "snapshot": snapshot}]
    result = await apply_batch_actions(await _approved_batch("order.cancel", actions), actions)
    assert result.applied is True
    assert result.effects == [{"type": "order_status", "order_id": order_id, "from": "pending", "to": "cancelled"}]
    async with SessionFactory() as session:
        row = await session.get(Order, order_id)
        assert row is not None and row.status == OrderStatus.CANCELLED


async def test_apply_cancel_from_delivered_is_conflict(product: Product) -> None:
    require_postgres()
    async with SessionFactory() as session:
        order = Order(product_id=product.id, status=OrderStatus.DELIVERED, total_amount=Decimal("9.99"))
        session.add(order)
        await session.commit()
        order_id = order.id
    snapshot = await make_executor().capture("order.cancel", {"order_id": order_id})
    actions = [{"action": "order.cancel", "params": {"order_id": order_id}, "snapshot": snapshot}]
    result = await apply_batch_actions(await _approved_batch("order.cancel", actions), actions)
    assert result.applied is False
    assert "不可取消" in (result.reason or "")


async def test_apply_delete_with_order_refs_is_conflict(product: Product) -> None:
    require_postgres()
    async with SessionFactory() as session:
        session.add(Order(product_id=product.id, status=OrderStatus.PENDING, total_amount=Decimal("9.99")))
        await session.commit()
    snapshot = await make_executor().capture("product.delete", {"product_id": product.id})
    actions = [{"action": "product.delete", "params": {"product_id": product.id}, "snapshot": snapshot}]
    result = await apply_batch_actions(await _approved_batch("product.delete", actions), actions)
    assert result.applied is False
    assert "订单" in (result.reason or "")


async def test_apply_delete_without_refs_deletes(product: Product) -> None:
    require_postgres()
    snapshot = await make_executor().capture("product.delete", {"product_id": product.id})
    actions = [{"action": "product.delete", "params": {"product_id": product.id}, "snapshot": snapshot}]
    result = await apply_batch_actions(await _approved_batch("product.delete", actions), actions)
    assert result.applied is True
    async with SessionFactory() as session:
        assert await session.get(Product, product.id) is None


async def test_apply_empty_batch_is_noop() -> None:
    assert (await apply_batch_actions("no-such-batch", [])).applied is True


async def test_apply_concurrent_same_product_serializes_no_dirty_write(product: Product) -> None:
    """B18:两个 apply 并发同商品同快照,恰好一个成功(行锁串行化 + 漂移检测)。"""
    require_postgres()
    snapshot_1 = await make_executor().capture("product.publish", {"product_id": product.id})
    snapshot_2 = dict(snapshot_1)

    async def apply(snapshot: dict) -> ApplyResult:
        actions = [{"action": "product.publish", "params": {"product_id": product.id}, "snapshot": snapshot}]
        return await apply_batch_actions(await _approved_batch("product.publish", actions), actions)

    result_1, result_2 = await asyncio.gather(apply(snapshot_1), apply(snapshot_2))

    applied = [r for r in (result_1, result_2) if r.applied is True]
    conflicted = [r for r in (result_1, result_2) if r.applied is False]
    assert len(applied) == 1
    assert len(conflicted) == 1
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None and row.status.value == "active"  # 无脏写:终态恰为一次 publish
