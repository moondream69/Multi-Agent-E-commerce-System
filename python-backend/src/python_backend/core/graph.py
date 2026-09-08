"""监督图(术语表:Manager + 业务子图组成的顶层状态图)。

宪章 ADR-0005 增量 2 骨架:
- manager 节点:ManagerPlanner 产出切片计划(或 PlanFailed 强制终止)
- prepare:计算拓扑分层(execution_order)
- 每层经条件边 Send 并行扇出(独立切片并行、依赖串行由分层保证;Pregel 语义下
  同层 Send 分支自动 join 后才推进 check_layer)
- execute_slice:调用业务 Agent(增量 4 前为注入的 stub;真实子图挂接点)
- aggregate/report_failure:汇总结果或如实上报未完成+原因

注意:增量 2 不挂 PostgresSaver(durable interrupt 属增量 3);
agents 以闭包注入而非 state 字段(可序列化要求,为增量 3 checkpointer 铺路)。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from python_backend.core.planning import ManagerPlanner, PlanFailed, Planner, Slice, SlicePlan


def merge_dicts(current: dict, update: dict) -> dict:
    """并行扇出结果合并 reducer(Annotated 约定:reducer(current, single_update))。"""
    result = dict(current)
    result.update(update)
    return result


class SupervisorState(TypedDict, total=False):
    request: str
    plan: SlicePlan | PlanFailed | None
    layers: list[list[int]]
    current_layer: int
    results: Annotated[dict[int, dict], merge_dicts]
    error: str | None
    summary: dict | None
    # Send 注入的切片数据(Send 状态为完整替换,execute_slice 经这些 key 取切片)
    slice_no: int
    slice: dict  # 完整切片字段(Slice 构造参数),无损往返——depends_on/approval_points 增量 3 审批断点要用


AgentRunner = Callable[[Slice], dict]


def _default_agent(slice_: Slice) -> dict:
    """业务子图占位(增量 4 替换为真实 Agent 子图):记录执行、返回占位结果。"""
    return {"agent": slice_.agent, "description": slice_.description, "executed": True}


def _manager_node(planner: Planner) -> Callable[[SupervisorState], Awaitable[dict]]:
    async def run(state: SupervisorState) -> dict:
        plan = await planner.plan(state["request"])
        return {"plan": plan}

    return run


def _after_manager(state: SupervisorState) -> str:
    if isinstance(state["plan"], PlanFailed):
        return "fail"
    return "prepare"


def _prepare(state: SupervisorState) -> dict:
    plan = state["plan"]
    assert isinstance(plan, SlicePlan)
    return {"layers": plan.execution_order(), "current_layer": 0, "results": {}}


def _next_step(state: SupervisorState) -> list[Send] | str:
    """条件边:当前层全部完成(join 语义保证)→ 发下一层 Send;全部层完成 → 汇总。

    Send 状态为完整替换(非合并),须携带切片完整数据——execute_slice 看不到主 state。
    """
    if state["current_layer"] >= len(state["layers"]):
        return "aggregate"
    plan = state["plan"]
    assert isinstance(plan, SlicePlan)
    by_no = {s.no: s for s in plan.slices}
    layer = state["layers"][state["current_layer"]]
    sends = []
    for no in layer:
        s = by_no[no]
        sends.append(
            Send(
                "execute_slice",
                {
                    "slice_no": no,
                    "slice": {
                        "no": s.no,
                        "agent": s.agent,
                        "description": s.description,
                        "depends_on": s.depends_on,
                        "approval_points": s.approval_points,
                    },
                },
            )
        )
    return sends


def _execute_slice(state: SupervisorState, agents: dict[str, AgentRunner]) -> dict:
    slice_ = Slice(**state["slice"])  # 无损重建:增量 3 起审批断点(approval_points)随切片流转
    runner = agents.get(slice_.agent, _default_agent)
    return {"results": {slice_.no: runner(slice_)}}


def _check_layer(state: SupervisorState) -> dict:
    """同层 Send 分支全部完成后执行(join),推进层号。"""
    return {"current_layer": state["current_layer"] + 1}


def _after_check(state: SupervisorState) -> list[Send] | str:
    return _next_step(state)


def _aggregate(state: SupervisorState) -> dict:
    """汇总(监督图定义:规划 → 分派 → 汇总):拼装切片轨迹与结果供上层/前端消费。"""
    plan = state["plan"]
    if isinstance(plan, SlicePlan):
        return {
            "error": state.get("error"),
            "summary": {
                "slices": [
                    {
                        "no": s.no,
                        "agent": s.agent,
                        "description": s.description,
                        "depends_on": s.depends_on,
                        "approval_points": s.approval_points,
                    }
                    for s in plan.slices
                ],
                "results": state.get("results", {}),
            },
        }
    return {"error": state.get("error"), "summary": {"slices": [], "results": {}}}


def _report_failure(state: SupervisorState) -> dict:
    plan = state["plan"]
    assert isinstance(plan, PlanFailed)
    return {"error": f"未完成:{plan.reason}"}


def build_supervisor(
    planner: Planner,
    *,
    agents: dict[str, AgentRunner] | None = None,
):
    """构建监督图:manager → prepare → 逐层 Send 扇出 → check_layer → … → aggregate。"""
    agents = agents or {}

    def execute_slice(state: SupervisorState) -> dict:
        return _execute_slice(state, agents)

    # langgraph 的 StateLike/_Node 泛型上界在静态检查下对具体 TypedDict 与
    # 逆变节点函数必然报 invalid-argument-type(运行时合法且为官方文档模式),故精确忽略。
    builder = StateGraph(SupervisorState)  # ty: ignore
    builder.add_node("manager", _manager_node(planner))  # ty: ignore
    builder.add_node("prepare", _prepare)
    builder.add_node("execute_slice", execute_slice)
    builder.add_node("check_layer", _check_layer)
    builder.add_node("aggregate", _aggregate)
    builder.add_node("report_failure", _report_failure)

    builder.add_edge(START, "manager")
    builder.add_conditional_edges("manager", _after_manager, {"prepare": "prepare", "fail": "report_failure"})
    builder.add_conditional_edges("prepare", _next_step)
    builder.add_edge("execute_slice", "check_layer")
    builder.add_conditional_edges("check_layer", _after_check)
    builder.add_edge("aggregate", END)
    builder.add_edge("report_failure", END)
    return builder.compile()


def default_supervisor() -> CompiledStateGraph:
    """生产装配入口:真实 ManagerPlanner + 占位业务 Agent(增量 4 换真实子图)。"""
    return build_supervisor(ManagerPlanner())
