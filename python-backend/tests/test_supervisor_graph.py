"""监督图骨架测试(seam S8/B1/B17):编译、单切片直达、依赖拓扑序、独立切片扇出、规划失败上报。"""

from __future__ import annotations

from collections.abc import Callable

from python_backend.core.graph import SupervisorState, build_supervisor
from python_backend.core.planning import PlanFailed, Slice, SlicePlan


class StubPlanner:
    def __init__(self, plan: SlicePlan | PlanFailed) -> None:
        self._plan = plan

    async def plan(self, request: str) -> SlicePlan | PlanFailed:
        return self._plan


def slice_agent(executed: list[int], *, record_order: bool = True) -> Callable[[Slice], dict]:
    def run(slice_: Slice) -> dict:
        if record_order:
            executed.append(slice_.no)
        return {"agent": slice_.agent, "description": slice_.description, "executed": True}

    return run


def plan(*slices: tuple[int, str, list[int]]) -> SlicePlan:
    return SlicePlan(
        slices=[
            Slice(no=no, agent=agent, description=f"切片 {no}", depends_on=deps, approval_points=[])
            for no, agent, deps in slices
        ]
    )


async def invoke(graph, request: str) -> dict:
    return await graph.ainvoke(SupervisorState(request=request))


async def test_supervisor_compiles_and_runs_single_slice() -> None:
    """B1:简单需求直达业务 Agent。"""
    executed: list[int] = []
    agents = {"order_management": slice_agent(executed)}
    graph = build_supervisor(StubPlanner(plan((1, "order_management", []))), agents=agents)

    result = await invoke(graph, "查库存")

    assert result["error"] is None
    assert result["results"][1]["executed"] is True
    assert executed == [1]
    assert [s["no"] for s in result["summary"]["slices"]] == [1]  # 汇总环节:切片轨迹拼装
    assert set(result["summary"]["results"]) == {1}


async def test_dependencies_run_in_topological_order() -> None:
    """依赖声明:切片 3 依赖 1、2,必须在两者之后执行。"""
    executed: list[int] = []
    agents = {
        "order_management": slice_agent(executed),
        "product_research": slice_agent(executed),
    }
    graph = build_supervisor(
        StubPlanner(plan((1, "order_management", []), (2, "product_research", []), (3, "order_management", [1, 2]))),
        agents=agents,
    )

    result = await invoke(graph, "先查库存和选品,再生成补货单")

    assert executed.index(3) > executed.index(1)
    assert executed.index(3) > executed.index(2)
    assert set(result["results"]) == {1, 2, 3}


async def test_independent_slices_all_execute() -> None:
    """独立切片并行扇出:全部执行且结果合并。"""
    executed: list[int] = []
    agents = {
        "order_management": slice_agent(executed),
        "product_research": slice_agent(executed),
        "customer_service": slice_agent(executed),
    }
    graph = build_supervisor(
        StubPlanner(plan((1, "order_management", []), (2, "product_research", []), (3, "customer_service", []))),
        agents=agents,
    )

    result = await invoke(graph, "三件事都要办")

    assert set(executed) == {1, 2, 3}
    assert set(result["results"]) == {1, 2, 3}
    assert result["error"] is None


async def test_plan_failed_reports_reason_and_skips_dispatch() -> None:
    """B17:规划强制终止 → 「未完成+原因」,不进入分派。"""
    executed: list[int] = []
    agents = {"order_management": slice_agent(executed)}
    graph = build_supervisor(StubPlanner(PlanFailed("规划步数超上限(6 > 5)")), agents=agents)

    result = await invoke(graph, "上架 6 个商品")

    assert result["error"] is not None
    assert "上限" in result["error"]
    assert result["results"] == {}
    assert executed == []
