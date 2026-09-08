"""审批断点(切片 1,spec #6 D1):带审批点的切片执行时挂起并落审批批次;影子模式只记录不挂起。

接缝:监督图公共接口(build_supervisor 注入 checkpointer + 批次存储协议)。
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from python_backend.core.approvals import BatchAlreadyDecidedError, classify_action
from python_backend.core.graph import SupervisorState, build_supervisor
from python_backend.core.planning import Slice, SlicePlan
from tests.conftest import InMemoryApprovalBatchStore, StubPlanner, slice_agent


def plan_with_approval() -> SlicePlan:
    return SlicePlan(
        slices=[Slice(no=1, agent="order_management", description="上架商品", approval_points=["上架审批"])]
    )


async def test_approval_slice_interrupts_and_creates_batch() -> None:
    """带审批点的切片:图挂起在 execute_slice,批次落库 pending/approval。"""
    store = InMemoryApprovalBatchStore()
    executed: list[int] = []
    graph = build_supervisor(
        StubPlanner(plan_with_approval()),
        agents={"order_management": slice_agent(executed)},
        checkpointer=InMemorySaver(),
        batch_store=store,
    )
    config = {"configurable": {"thread_id": "approval-thread-1"}}

    # ainvoke 遇 interrupt 不抛异常,返回带 __interrupt__ 的 state(行为钉:探针验证)
    result = await graph.ainvoke(SupervisorState(request="上架商品", thread_id="approval-thread-1"), config)
    assert "__interrupt__" in result

    snapshot = await graph.aget_state(config)
    assert "execute_slice" in snapshot.next, f"应挂起在 execute_slice,实际 next={snapshot.next}"
    assert len(snapshot.tasks) == 1
    assert len(snapshot.tasks[0].interrupts) == 1

    payload = snapshot.tasks[0].interrupts[0].value
    assert payload["slice_no"] == 1
    assert payload["batch_id"]

    assert len(store.batches) == 1
    batch = store.batches[0]
    assert batch.batch_id == payload["batch_id"]
    assert batch.thread_id == "approval-thread-1"
    assert batch.slice_no == 1
    assert batch.action_type == "上架审批"
    assert batch.status == "pending"
    assert batch.mode == "approval"
    assert executed == [], "审批决定前业务逻辑不得执行"


async def test_shadow_mode_records_batch_without_interrupt() -> None:
    """影子模式:批次落库 mode=shadow,图不挂起直行。"""
    store = InMemoryApprovalBatchStore()
    executed: list[int] = []
    graph = build_supervisor(
        StubPlanner(plan_with_approval()),
        agents={"order_management": slice_agent(executed)},
        checkpointer=InMemorySaver(),
        batch_store=store,
        shadow_mode=True,
    )
    config = {"configurable": {"thread_id": "shadow-thread-1"}}

    result = await graph.ainvoke(SupervisorState(request="上架商品", thread_id="shadow-thread-1"), config)

    assert result["results"][1]["executed"] is True
    assert executed == [1]
    assert len(store.batches) == 1
    assert store.batches[0].mode == "shadow"
    assert store.batches[0].status == "shadow"


async def _pending_interrupt_id(graph, config: dict) -> str:
    """取当前挂起中断的 id(生产同路径:aget_state 稳定 API)。"""
    snapshot = await graph.aget_state(config)
    return snapshot.tasks[0].interrupts[0].id


async def test_approve_decision_resumes_and_executes_slice() -> None:
    """决定 approve:切片从断点继续执行,批次状态落 approved。"""
    store = InMemoryApprovalBatchStore()
    executed: list[int] = []
    graph = build_supervisor(
        StubPlanner(plan_with_approval()),
        agents={"order_management": slice_agent(executed)},
        checkpointer=InMemorySaver(),
        batch_store=store,
    )
    config = {"configurable": {"thread_id": "decide-approve"}}
    await graph.ainvoke(SupervisorState(request="上架商品", thread_id="decide-approve"), config)

    await graph.ainvoke(Command(resume={await _pending_interrupt_id(graph, config): {"decision": "approve"}}), config)

    final = await graph.aget_state(config)
    assert final.values["results"][1]["executed"] is True
    assert executed == [1]
    assert store.batches[0].status == "approved"


async def test_reject_decision_marks_slice_rejected_without_execution() -> None:
    """决定 reject:业务不执行,结果带拒因标记,批次状态落 rejected。"""
    store = InMemoryApprovalBatchStore()
    executed: list[int] = []
    graph = build_supervisor(
        StubPlanner(plan_with_approval()),
        agents={"order_management": slice_agent(executed)},
        checkpointer=InMemorySaver(),
        batch_store=store,
    )
    config = {"configurable": {"thread_id": "decide-reject"}}
    await graph.ainvoke(SupervisorState(request="上架商品", thread_id="decide-reject"), config)

    await graph.ainvoke(
        Command(resume={await _pending_interrupt_id(graph, config): {"decision": "reject", "comment": "价格太低"}}),
        config,
    )

    final = await graph.aget_state(config)
    result = final.values["results"][1]
    assert "executed" not in result
    assert result["rejected"] is True
    assert result["comment"] == "价格太低"
    assert executed == []
    assert store.batches[0].status == "rejected"
    assert store.batches[0].comment == "价格太低"


async def test_terminate_decision_ends_thread_without_replan() -> None:
    """用户终止:批次落 rejected,图「用户终止」结束,不回流重规划。"""
    store = InMemoryApprovalBatchStore()
    executed: list[int] = []
    graph = build_supervisor(
        StubPlanner(plan_with_approval()),
        agents={"order_management": slice_agent(executed)},
        checkpointer=InMemorySaver(),
        batch_store=store,
    )
    config = {"configurable": {"thread_id": "decide-terminate"}}
    await graph.ainvoke(SupervisorState(request="上架商品", thread_id="decide-terminate"), config)

    await graph.ainvoke(
        Command(
            resume={
                await _pending_interrupt_id(graph, config): {
                    "decision": "reject",
                    "terminate": True,
                    "comment": "算了",
                }
            }
        ),
        config,
    )

    final = await graph.aget_state(config)
    assert final.values["error"] == "用户终止任务"
    result = final.values["results"][1]
    assert result["terminated"] is True
    assert executed == []
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
        batch_id="b1", thread_id="t", slice_no=1, action_type="上架审批", actions=[], mode="approval"
    )
    await store.decide_batch(batch_id="b1", decision="approve")
    await store.decide_batch(batch_id="b1", decision="approve")  # 同决定重放:幂等返回,不抛

    with pytest.raises(BatchAlreadyDecidedError):
        await store.decide_batch(batch_id="b1", decision="reject")
    assert store._by_id["b1"].status == "approved", "冲突决定不得覆盖首次决定"
