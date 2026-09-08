"""拒后重规划图内回流(切片 3,spec #6 D3):被拒 → 携拒因回 manager 重规划;冲突/超限强制终止。

接缝:监督图公共接口(build_supervisor + Planner 协议注入)。
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from python_backend.core.graph import SupervisorState, build_supervisor
from python_backend.core.planning import Slice, SlicePlan
from tests.conftest import InMemoryApprovalBatchStore, slice_agent


class ScriptedPlanner:
    """依次返回预设计划;捕获每次规划收到的请求文本。"""

    def __init__(self, plans: list[SlicePlan]) -> None:
        self._plans = list(plans)
        self.requests: list[str] = []

    async def plan(self, request: str) -> SlicePlan:
        self.requests.append(request)
        return self._plans.pop(0) if self._plans else self._plans[-1]


async def _start(graph, config: dict) -> None:
    """发起任务:跑至挂起(ainvoke 遇 interrupt 返回带 __interrupt__ 的 state,不抛异常)。"""
    result = await graph.ainvoke(
        SupervisorState(request="上架商品", thread_id=config["configurable"]["thread_id"]), config
    )
    assert "__interrupt__" in result, "首轮应挂起在审批断点"


async def _reject_pending(graph, config: dict, comment: str = "价格太低") -> None:
    """对当前挂起中断提交 reject 决定。"""
    snapshot = await graph.aget_state(config)
    iid = snapshot.tasks[0].interrupts[0].id
    await graph.ainvoke(Command(resume={iid: {"decision": "reject", "comment": comment}}), config)


async def test_rejected_slice_triggers_replan_with_rejection_reason() -> None:
    """B3 核心:切片被拒 → manager 携拒因重规划 → 替代路径(新切片号)执行完成。"""
    plans = [
        SlicePlan(
            slices=[
                Slice(no=1, agent="order_management", description="上架商品", approval_points=["上架审批"]),
                Slice(no=2, agent="order_management", description="发上架通知", depends_on=[1]),
            ]
        ),
        SlicePlan(slices=[Slice(no=3, agent="order_management", description="改走人工上架路径")]),
    ]
    planner = ScriptedPlanner(plans)
    executed: list[int] = []
    graph = build_supervisor(
        planner,
        agents={"order_management": slice_agent(executed)},
        checkpointer=InMemorySaver(),
        batch_store=InMemoryApprovalBatchStore(),
    )
    config = {"configurable": {"thread_id": "replan-basic"}}

    await _start(graph, config)
    await _reject_pending(graph, config)

    final = await graph.aget_state(config)
    results = final.values["results"]
    assert results[1]["rejected"] is True
    assert results[3]["executed"] is True
    assert 2 not in results, "被拒切片的下游依赖不得执行"
    assert final.values["error"] is None

    # 重规划请求携带拒因与已完成上下文
    assert len(planner.requests) == 2
    replan_request = planner.requests[1]
    assert "价格太低" in replan_request
    assert "切片 1" in replan_request


async def test_replan_with_conflicting_slice_no_terminates_with_reason() -> None:
    """重规划切片号与已完成切片冲突 → 强制终止「未完成+原因」。"""
    plans = [
        SlicePlan(slices=[Slice(no=1, agent="order_management", description="上架商品", approval_points=["上架审批"])]),
        SlicePlan(slices=[Slice(no=1, agent="order_management", description="换个方式上架")]),
    ]
    planner = ScriptedPlanner(plans)
    executed: list[int] = []
    graph = build_supervisor(
        planner,
        agents={"order_management": slice_agent(executed)},
        checkpointer=InMemorySaver(),
        batch_store=InMemoryApprovalBatchStore(),
    )
    config = {"configurable": {"thread_id": "replan-conflict"}}

    await _start(graph, config)
    await _reject_pending(graph, config)

    final = await graph.aget_state(config)
    assert final.values["error"] is not None
    assert "冲突" in final.values["error"]
    assert executed == [], "冲突计划不得执行任何切片"


async def test_replan_loop_hits_limit_and_terminates() -> None:
    """连续重规划超限 → 强制终止,不无限回流。"""
    # 每次重规划产出新的审批切片(切片号续编合法),连续拒绝直到回流超限
    plans = [
        SlicePlan(slices=[Slice(no=n, agent="order_management", description="上架商品", approval_points=["上架审批"])])
        for n in (1, 2, 3)
    ]
    planner = ScriptedPlanner(plans)
    executed: list[int] = []
    graph = build_supervisor(
        planner,
        agents={"order_management": slice_agent(executed)},
        checkpointer=InMemorySaver(),
        batch_store=InMemoryApprovalBatchStore(),
    )
    config = {"configurable": {"thread_id": "replan-limit"}}

    await _start(graph, config)
    for _ in range(3):  # 首轮 + 两次重规划(REPLAN_LIMIT=2)
        await _reject_pending(graph, config)

    final = await graph.aget_state(config)
    assert final.values["error"] is not None
    assert "重规划次数超限" in final.values["error"]
    assert executed == []
