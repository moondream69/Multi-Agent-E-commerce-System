"""审批断点(spec #7):子图收集审批动作 → 按类型打包批次(真实参数快照)→ 边界 interrupt;
决定后 apply 已批批次(接缝注入);影子模式只记录不阻塞。

接缝:监督图公共接口(build_supervisor 注入 checkpointer + 批次存储协议 + apply_fn)。
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from python_backend.agents.executor import ApplyResult
from python_backend.core.approvals import BatchAlreadyDecidedError, classify_action
from python_backend.core.graph import SupervisorState, build_supervisor
from python_backend.core.planning import Slice, SlicePlan
from tests.conftest import FakeApply, InMemoryApprovalBatchStore, RecordingEmitter, StubPlanner, slice_agent

PUBLISH = {
    "action": "product.publish",
    "params": {"product_id": 1},
    "snapshot": {"exists": True, "status": "draft", "title": "宠物饮水机"},
}
PRICE = {
    "action": "product.update_price",
    "params": {"product_id": 2, "new_price": "29.9"},
    "snapshot": {"exists": True, "price": "9.9"},
}


def plan_with_actions() -> SlicePlan:
    return SlicePlan(slices=[Slice(no=1, agent="order_management", description="上架并改价")])


async def _pending_interrupt(graph, config: dict) -> tuple[str, dict]:
    """取当前挂起中断的 (id, value)。"""
    snapshot = await graph.aget_state(config)
    interrupt_ = snapshot.tasks[0].interrupts[0]
    return interrupt_.id, interrupt_.value


def _decisions(value: dict, decision: str, comment: str | None = None) -> dict:
    """组装 spec #7 决定载荷:terminate 标志 + 逐批决定。"""
    return {
        "terminate": False,
        "decisions": {
            batch["batch_id"]: {"decision": decision, "comment": comment} for batch in value.get("batches", [])
        },
    }


async def test_actions_batched_by_type_then_interrupt() -> None:
    """B4/B7:审批动作按类型打包(不跨类型混批),边界 interrupt 载荷携带全部批次。"""
    store = InMemoryApprovalBatchStore()
    executed: list[int] = []
    graph = build_supervisor(
        StubPlanner(plan_with_actions()),
        agents={"order_management": slice_agent(executed, actions=[PUBLISH, PRICE, dict(PUBLISH)])},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=FakeApply(),
    )
    config = {"configurable": {"thread_id": "batch-by-type"}}

    result = await graph.ainvoke(SupervisorState(request="上架并改价", thread_id="batch-by-type"), config)
    assert "__interrupt__" in result

    _iid, payload = await _pending_interrupt(graph, config)
    assert payload["slice_no"] == 1
    batches = payload["batches"]
    assert [b["action_type"] for b in batches] == ["product.publish", "product.update_price"]
    assert len(batches[0]["actions"]) == 2, "同类动作同批"
    assert batches[0]["actions"][0]["params"] == {"product_id": 1}, "真实参数快照"
    assert len(batches[1]["actions"]) == 1

    assert len(store.batches) == 2
    assert executed == [1], "方案 B:子图先运行收集,边界打包挂起"


async def test_approve_emits_notifications_from_effects() -> None:
    """spec #9:apply 提交后按效果广播 notification.created(效果在事务内产生、提交后 emit)。"""
    store = InMemoryApprovalBatchStore()
    emitter = RecordingEmitter()

    async def apply_fn(batch_id: str, actions: list[dict]) -> ApplyResult:
        return ApplyResult(
            applied=True,
            effects=[{"type": "order_status", "order_id": 42, "from": "pending", "to": "shipped"}],
        )

    graph = build_supervisor(
        StubPlanner(plan_with_actions()),
        agents={"order_management": slice_agent([], actions=[PUBLISH])},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=apply_fn,
        emitter=emitter,
    )
    config = {"configurable": {"thread_id": "notify-effects"}}
    await graph.ainvoke(SupervisorState(request="上架商品", thread_id="notify-effects"), config)
    iid, value = await _pending_interrupt(graph, config)
    await graph.ainvoke(Command(resume={iid: _decisions(value, "approve")}), config)

    notifications = [payload for event, payload in emitter.events if event == "notification.created"]
    assert len(notifications) == 1
    assert notifications[0]["kind"] == "order_status"
    assert "#42" in notifications[0]["message"]


async def test_reject_emits_no_notification() -> None:
    """拒绝不 apply、不广播业务通知(只有审批决定事件)。"""
    store = InMemoryApprovalBatchStore()
    emitter = RecordingEmitter()

    async def apply_fn(batch_id: str, actions: list[dict]) -> ApplyResult:
        raise AssertionError("拒绝不应调用 apply")

    graph = build_supervisor(
        StubPlanner(plan_with_actions()),
        agents={"order_management": slice_agent([], actions=[PUBLISH])},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=apply_fn,
        emitter=emitter,
    )
    config = {"configurable": {"thread_id": "reject-no-notify"}}
    await graph.ainvoke(SupervisorState(request="上架商品", thread_id="reject-no-notify"), config)
    iid, value = await _pending_interrupt(graph, config)
    await graph.ainvoke(Command(resume={iid: _decisions(value, "reject", "不要上架")}), config)

    assert [event for event, _payload in emitter.events if event == "notification.created"] == []


async def test_shadow_mode_records_batches_without_interrupt() -> None:
    """影子模式:批次落库 mode=shadow/status=shadow,图不挂起直行,不 apply。"""
    store = InMemoryApprovalBatchStore()
    executed: list[int] = []
    apply_fn = FakeApply()
    graph = build_supervisor(
        StubPlanner(plan_with_actions()),
        agents={"order_management": slice_agent(executed, actions=[PUBLISH])},
        checkpointer=InMemorySaver(),
        batch_store=store,
        shadow_mode=True,
        apply_fn=apply_fn,
    )
    config = {"configurable": {"thread_id": "shadow-records"}}

    result = await graph.ainvoke(SupervisorState(request="上架商品", thread_id="shadow-records"), config)

    assert result["results"][1]["executed"] is True
    assert result["results"][1]["shadow_batches"] == 1
    assert executed == [1]
    assert len(store.batches) == 1
    assert store.batches[0].mode == "shadow"
    assert store.batches[0].status == "shadow"
    assert apply_fn.calls == [], "影子模式不执行效果"


async def test_approve_applies_batches() -> None:
    """批准:已批批次逐个 apply(接缝注入的假实现),结果无冲突,任务完成。"""
    store = InMemoryApprovalBatchStore()
    apply_fn = FakeApply()
    graph = build_supervisor(
        StubPlanner(plan_with_actions()),
        agents={"order_management": slice_agent([], actions=[PUBLISH, PRICE])},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=apply_fn,
    )
    config = {"configurable": {"thread_id": "approve-apply"}}
    await graph.ainvoke(SupervisorState(request="上架并改价", thread_id="approve-apply"), config)

    iid, value = await _pending_interrupt(graph, config)
    await graph.ainvoke(Command(resume={iid: _decisions(value, "approve")}), config)

    final = await graph.aget_state(config)
    assert final.values["results"][1]["executed"] is True
    assert "conflict" not in final.values["results"][1]
    assert sorted(b for b, _a in apply_fn.calls) == sorted(b["batch_id"] for b in value["batches"])
    assert [b.status for b in store.batches] == ["approved", "approved"]


async def test_reject_marks_rejected_without_apply() -> None:
    """拒绝:不 apply,结果带拒因标记,批次落 rejected(回流重规划路径)。"""
    store = InMemoryApprovalBatchStore()
    apply_fn = FakeApply()
    graph = build_supervisor(
        StubPlanner(plan_with_actions()),
        agents={"order_management": slice_agent([], actions=[PUBLISH])},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=apply_fn,
    )
    config = {"configurable": {"thread_id": "reject-no-apply"}}
    await graph.ainvoke(SupervisorState(request="上架商品", thread_id="reject-no-apply"), config)

    iid, value = await _pending_interrupt(graph, config)
    await graph.ainvoke(Command(resume={iid: _decisions(value, "reject", "价格太低")}), config)

    final = await graph.aget_state(config)
    result = final.values["results"][1]
    assert result["rejected"] is True
    assert result["comment"] == "价格太低"
    assert apply_fn.calls == []
    assert store.batches[0].status == "rejected"
    assert store.batches[0].comment == "价格太低"


async def test_mixed_decision_applies_only_approved() -> None:
    """逐批决定:一批批一批拒 → 只 apply 批准批,整体标记被拒(回流)。"""
    store = InMemoryApprovalBatchStore()
    apply_fn = FakeApply()
    graph = build_supervisor(
        StubPlanner(plan_with_actions()),
        agents={"order_management": slice_agent([], actions=[PUBLISH, PRICE])},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=apply_fn,
    )
    config = {"configurable": {"thread_id": "mixed-decision"}}
    await graph.ainvoke(SupervisorState(request="上架并改价", thread_id="mixed-decision"), config)

    iid, value = await _pending_interrupt(graph, config)
    publish_batch = next(b for b in value["batches"] if b["action_type"] == "product.publish")
    price_batch = next(b for b in value["batches"] if b["action_type"] == "product.update_price")
    await graph.ainvoke(
        Command(
            resume={
                iid: {
                    "terminate": False,
                    "decisions": {
                        publish_batch["batch_id"]: {"decision": "approve"},
                        price_batch["batch_id"]: {"decision": "reject", "comment": "价格没谈好"},
                    },
                }
            }
        ),
        config,
    )

    final = await graph.aget_state(config)
    result = final.values["results"][1]
    assert result["rejected"] is True
    assert [bid for bid, _a in apply_fn.calls] == [publish_batch["batch_id"]]
    assert store._by_id[price_batch["batch_id"]].status == "rejected"


async def test_apply_conflict_reported_honestly() -> None:
    """B18:apply 冲突如实上报为任务 error(不静默)。"""
    store = InMemoryApprovalBatchStore()
    apply_fn = FakeApply()
    graph = build_supervisor(
        StubPlanner(plan_with_actions()),
        agents={"order_management": slice_agent([], actions=[PUBLISH])},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=apply_fn,
    )
    config = {"configurable": {"thread_id": "apply-conflict"}}
    await graph.ainvoke(SupervisorState(request="上架商品", thread_id="apply-conflict"), config)

    from python_backend.agents.executor import ApplyResult

    iid, value = await _pending_interrupt(graph, config)
    batch_id = value["batches"][0]["batch_id"]
    apply_fn._outcomes[batch_id] = ApplyResult(applied=False, reason="商品价格已变化")  # 决定前制造漂移冲突
    await graph.ainvoke(Command(resume={iid: _decisions(value, "approve")}), config)

    final = await graph.aget_state(config)
    assert final.values["results"][1]["conflict"]
    assert final.values["error"] and "冲突" in final.values["error"]


async def test_terminate_ends_thread_without_replan_or_apply() -> None:
    """用户终止:批次落 rejected,图「用户终止」结束,不回流重规划、不 apply。"""
    store = InMemoryApprovalBatchStore()
    apply_fn = FakeApply()
    graph = build_supervisor(
        StubPlanner(plan_with_actions()),
        agents={"order_management": slice_agent([], actions=[PUBLISH])},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=apply_fn,
    )
    config = {"configurable": {"thread_id": "terminate-new"}}
    await graph.ainvoke(SupervisorState(request="上架商品", thread_id="terminate-new"), config)

    iid, value = await _pending_interrupt(graph, config)
    await graph.ainvoke(
        Command(
            resume={
                iid: {
                    "terminate": True,
                    "decisions": {b["batch_id"]: {"decision": "reject", "comment": "算了"} for b in value["batches"]},
                }
            }
        ),
        config,
    )

    final = await graph.aget_state(config)
    assert final.values["error"] == "用户终止任务"
    result = final.values["results"][1]
    assert result["terminated"] is True
    assert apply_fn.calls == []
    assert store.batches[0].status == "rejected"


def test_three_tier_risk_classification() -> None:
    """三层分类(B7 语义):免审直行、审批进护栏、禁做/未知不暴露。"""
    assert classify_action("draft.edit") == "auto"
    assert classify_action("draft.create") == "auto"
    assert classify_action("product.publish") == "approval"
    assert classify_action("product.unpublish") == "approval"
    assert classify_action("product.update_price") == "approval"
    assert classify_action("product.delete") == "approval"
    assert classify_action("order.transition") == "approval"
    assert classify_action("order.cancel") == "approval"
    assert classify_action("anything.unknown") == "forbidden"


async def test_decide_batch_same_decision_is_idempotent_conflict_raises() -> None:
    """批次幂等(spec #6 D5):同决定幂等返回(durable 重放),冲突决定明确拒绝。"""
    store = InMemoryApprovalBatchStore()
    await store.create_batch(
        batch_id="b1", thread_id="t", slice_no=1, action_type="product.publish", actions=[], mode="approval"
    )
    await store.decide_batch(batch_id="b1", decision="approve")
    await store.decide_batch(batch_id="b1", decision="approve")  # 同决定重放:幂等返回,不抛

    with pytest.raises(BatchAlreadyDecidedError):
        await store.decide_batch(batch_id="b1", decision="reject")
    assert store._by_id["b1"].status == "approved", "冲突决定不得覆盖首次决定"
