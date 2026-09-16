"""监督图骨架测试(seam S8/B1/B17):编译、单切片直达、依赖拓扑序、独立切片扇出、规划失败上报。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from python_backend.core.graph import SupervisorState, build_supervisor
from python_backend.core.planning import PlanFailed, Slice, SlicePlan
from tests.conftest import RecordingAudit


class StubPlanner:
    def __init__(self, plan: SlicePlan | PlanFailed) -> None:
        self._plan = plan

    async def plan(self, request: str, context: str | None = None) -> SlicePlan | PlanFailed:
        return self._plan


def slice_agent(executed: list[int], *, record_order: bool = True) -> Callable[[Slice, str], Awaitable[dict]]:
    async def run(slice_: Slice, _task_request: str) -> dict:
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
    assert result["summary"] == "完成 1/1 个切片"  # 汇总环节:人类可读摘要(issue #25 字符串口径)


async def test_request_travels_with_slice_to_runner() -> None:
    """#64 B2:原请求随切片下发到执行段。

    Send 状态是**完整替换**——execute_slice 看不到主 state,原请求必须由 Send 载荷自己带;
    否则执行段只拿到切片描述,描述缺主语时只能猜(2026-09-16 实录:猜成「宠物饮水机」)。
    """
    received: list[str] = []

    async def run(slice_: Slice, task_request: str) -> dict:
        received.append(task_request)
        return {"agent": slice_.agent, "description": slice_.description, "executed": True}

    graph = build_supervisor(StubPlanner(plan((1, "order_management", []))), agents={"order_management": run})

    await invoke(graph, "分析一下便携咖啡机在美国市场的选品机会")

    assert received == ["分析一下便携咖啡机在美国市场的选品机会"]


async def test_citations_ride_along_with_answer_into_results_and_audit() -> None:
    """issue #51:子图产出的引用条目随答案同份下发(切片结果与审计 run_output 同源)。"""
    citation = {
        "number": 1,
        "doc_id": "faq-returns",
        "title": "退款多久到账?退到哪里?",
        "source": "自造 FAQ 语料库",
        "published_at": "2026-09-14",
        "chunks": [
            {"id": "faq-returns#6", "score": 0.83, "section": "退货退款", "chunk_index": 6, "content": "1-3 天"}
        ],
    }

    async def run(slice_: Slice, _task_request: str) -> dict:
        return {"actions": [], "answer": "1-3 个工作日到账[1]。", "incomplete": None, "citations": [citation]}

    audit = RecordingAudit()
    graph = build_supervisor(
        StubPlanner(plan((1, "customer_service", []))), agents={"customer_service": run}, audit=audit
    )

    result = await invoke(graph, "退款多久到账?")

    assert result["results"][1]["answer"] == "1-3 个工作日到账[1]。"
    assert result["results"][1]["citations"] == [citation]
    slice_record = next(record for record in audit.captured if record["type"] == "slice")
    assert slice_record["output"]["citations"] == [citation]  # 批次 run_output 与审计同一份


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
    assert result.get("summary") is None  # 规划失败不产摘要(issue #25 边界)
    assert executed == []
