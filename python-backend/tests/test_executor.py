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
from python_backend.settings import get_settings
from python_backend.vector_repo.base import VectorRecord
from tests.conftest import FakeEmbedding, FakeLlm, InMemoryVectorRepository, postgres_reachable


def _pg_guard() -> None:
    if not postgres_reachable(get_settings().database_url):
        pytest.skip("Postgres 离线(compose dev 库),integration 跳过")


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
        "check_inventory",
        "detect_anomalies",
        "list_approvals",
        "faq_search",
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


async def test_execute_generate_report_and_draft() -> None:
    llm = FakeLlm(responses=["# 选品报告\\n\\n结论:值得做。", "买家您好,已为您查询。"])
    executor = make_executor(llm)
    report = await executor.execute("generate_report", {"context": "宠物市场"})
    assert report["report"].startswith("# 选品报告")
    draft = await executor.execute("generate_draft", {"buyer_message": "我的订单到哪了?", "evidence": "已发货"})
    assert draft["draft"] == "买家您好,已为您查询。"


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


# —— integration:DB 工具(capture/apply 见下)——


@pytest.fixture
async def product() -> Product:
    _pg_guard()
    async with SessionFactory() as session:
        row = Product(sku=f"SKU-{uuid.uuid4().hex[:8]}", title="宠物饮水机", price=Decimal("9.99"), category="宠物")
        session.add(row)
        await session.commit()
        return row


async def test_execute_draft_create_inserts_draft(product: Product) -> None:
    _pg_guard()
    result = await make_executor().execute(
        "draft.create",
        {"sku": f"SKU-{uuid.uuid4().hex[:8]}", "title": "自动饮水机", "price": "19.9", "category": "宠物"},
    )
    assert result["status"] == "draft"
    async with SessionFactory() as session:
        row = await session.get(Product, int(result["product_id"]))
        assert row is not None and row.status.value == "draft"


async def test_execute_draft_create_duplicate_sku_raises(product: Product) -> None:
    _pg_guard()
    with pytest.raises(ValueError, match="已存在"):
        await make_executor().execute(
            "draft.create", {"sku": product.sku, "title": "重复", "price": "1.0", "category": "宠物"}
        )


async def test_execute_draft_edit_updates_only_draft(product: Product) -> None:
    _pg_guard()
    executor = make_executor()
    result = await executor.execute("draft.edit", {"product_id": product.id, "title": "新版标题", "price": "12.5"})
    assert result["title"] == "新版标题"
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None and row.price == Decimal("12.50")


async def test_execute_draft_edit_non_draft_raises(product: Product) -> None:
    _pg_guard()
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None
        row.status = ProductStatus.ACTIVE
        await session.commit()
    with pytest.raises(ValueError, match="仅草稿"):
        await make_executor().execute("draft.edit", {"product_id": product.id, "title": "非法编辑"})


async def test_execute_draft_create_with_alert_threshold(product: Product) -> None:
    """spec #9:草稿创建可带库存告警阈值(缺省取默认 10)。"""
    _pg_guard()
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
    _pg_guard()
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
    _pg_guard()
    result = await make_executor().execute("draft.edit", {"product_id": product.id, "alert_threshold": 7})
    assert result["alert_threshold"] == 7
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None and row.alert_threshold == 7


async def test_execute_check_inventory_reads_real_stock(product: Product) -> None:
    """A7:库存检查读库(不再 LLM 自报),五档告警文案。"""
    _pg_guard()
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
    _pg_guard()
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
    _pg_guard()
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
    _pg_guard()
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
    _pg_guard()
    result = await make_executor().execute("escalate_ticket", {"message": "客户要求升级处理"})
    async with SessionFactory() as session:
        rows = (await session.execute(select(Ticket))).scalars().all()
        assert any(row.id == result["ticket_id"] and row.message == "客户要求升级处理" for row in rows)


async def test_execute_manage_template_crud() -> None:
    _pg_guard()
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
    _pg_guard()
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


# —— integration:capture 现状快照 ——


async def test_capture_product_snapshot(product: Product) -> None:
    _pg_guard()
    snapshot = await make_executor().capture("product.publish", {"product_id": product.id})
    assert snapshot == {"exists": True, "status": "draft", "title": "宠物饮水机", "sku": product.sku}


async def test_capture_missing_product_marks_not_exists() -> None:
    _pg_guard()
    snapshot = await make_executor().capture("product.publish", {"product_id": 999999})
    assert snapshot == {"exists": False}


async def test_capture_order_transition_snapshot(product: Product) -> None:
    _pg_guard()
    async with SessionFactory() as session:
        order = Order(product_id=product.id, status=OrderStatus.PENDING, total_amount=Decimal("9.99"))
        session.add(order)
        await session.commit()
        snapshot = await make_executor().capture("order.transition", {"order_id": order.id})
        assert snapshot["status"] == "pending"


async def test_capture_unknown_action_raises() -> None:
    _pg_guard()
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
    _pg_guard()
    snapshot = await make_executor().capture("product.publish", {"product_id": product.id})
    actions = [{"action": "product.publish", "params": {"product_id": product.id}, "snapshot": snapshot}]
    result = await apply_batch_actions(await _approved_batch("product.publish", actions), actions)
    assert result.applied is True
    async with SessionFactory() as session:
        row = await session.get(Product, product.id)
        assert row is not None and row.status.value == "active"


async def test_apply_update_price_happy_path(product: Product) -> None:
    _pg_guard()
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
    _pg_guard()
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
    _pg_guard()
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
    _pg_guard()
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
    _pg_guard()
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
    _pg_guard()
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
    _pg_guard()
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
    _pg_guard()
    async with SessionFactory() as session:
        session.add(Order(product_id=product.id, status=OrderStatus.PENDING, total_amount=Decimal("9.99")))
        await session.commit()
    snapshot = await make_executor().capture("product.delete", {"product_id": product.id})
    actions = [{"action": "product.delete", "params": {"product_id": product.id}, "snapshot": snapshot}]
    result = await apply_batch_actions(await _approved_batch("product.delete", actions), actions)
    assert result.applied is False
    assert "订单" in (result.reason or "")


async def test_apply_delete_without_refs_deletes(product: Product) -> None:
    _pg_guard()
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
    _pg_guard()
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
