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

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send, interrupt

from python_backend.core.approvals import ApprovalBatchStore
from python_backend.core.planning import ManagerPlanner, PlanFailed, Planner, Slice, SlicePlan


def supervisor_serde() -> JsonPlusSerializer:
    """监督图 serde:规划类型注册进 msgpack 允许列表(durable checkpoint 序列化必需)。

    不注册时 jsonplus 以 legacy 模式往返(告警提示未来版本将 blocked)。
    """
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("python_backend.core.planning", "Slice"),
            ("python_backend.core.planning", "SlicePlan"),
            ("python_backend.core.planning", "PlanFailed"),
        ]
    )


def merge_dicts(current: dict, update: dict) -> dict:
    """并行扇出结果合并 reducer(Annotated 约定:reducer(current, single_update))。"""
    result = dict(current)
    result.update(update)
    return result


class SupervisorState(TypedDict, total=False):
    request: str
    thread_id: str
    plan: SlicePlan | PlanFailed | None
    layers: list[list[int]]
    current_layer: int
    replan_count: int
    results: Annotated[dict[int, dict], merge_dicts]
    error: str | None
    summary: dict | None
    # Send 注入的切片数据(Send 状态为完整替换,execute_slice 经这些 key 取切片)
    slice_no: int
    slice: dict  # 完整切片字段(Slice 构造参数),无损往返——depends_on/approval_points 增量 3 审批断点要用


AgentRunner = Callable[[Slice], dict]

# 重规划回流次数上限(spec #6 D3):超限强制终止,防 LLM 反复产出被拒计划
REPLAN_LIMIT = 2


def _default_agent(slice_: Slice) -> dict:
    """业务子图占位(增量 4 替换为真实 Agent 子图):记录执行、返回占位结果。"""
    return {"agent": slice_.agent, "description": slice_.description, "executed": True}


def _replan_prompt(request: str, results: dict[int, dict]) -> str:
    """重规划提示:原需求 + 已完成切片结果 + 拒因 + 切片号续编指令。"""
    done = [f"- 切片 {no}:{_brief(r)}" for no, r in sorted(results.items())]
    rejected = [no for no, r in results.items() if r.get("rejected")]
    max_no = max(results) if results else 0
    lines = [
        f"原需求:{request}",
        "",
        "已完成切片:",
        *done,
        "",
        f"切片 {rejected} 被拒绝。请携拒绝原因重规划剩余工作:跳过被拒动作或改走替代路径;"
        f"新切片编号必须大于 {max_no} 且不与被拒切片重复;无法继续时如实说明并终止。",
    ]
    return "\n".join(lines)


def _brief(result: dict) -> str:
    if result.get("rejected"):
        return f"被拒({result.get('comment', '')})"
    return "已完成"


def _manager_node(planner: Planner) -> Callable[[SupervisorState], Awaitable[dict]]:
    async def run(state: SupervisorState) -> dict:
        results = state.get("results", {})
        if not any(r.get("rejected") for r in results.values()):
            return {"plan": await planner.plan(state["request"])}

        # 重规划路径:携拒因 + 已完成上下文;超限强制终止;切片号冲突强制终止
        replan_count = state.get("replan_count", 0) + 1
        if replan_count > REPLAN_LIMIT:
            return {"plan": PlanFailed(f"重规划次数超限({REPLAN_LIMIT})"), "replan_count": replan_count}
        plan = await planner.plan(_replan_prompt(state["request"], results))
        if isinstance(plan, SlicePlan):
            conflicts = sorted(no for no in (s.no for s in plan.slices) if no in results)
            if conflicts:
                plan = PlanFailed(f"重规划切片号与已完成切片冲突:{conflicts}")
        return {"plan": plan, "replan_count": replan_count}

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
                    "thread_id": state.get("thread_id", ""),
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


async def _execute_slice(
    state: SupervisorState,
    agents: dict[str, AgentRunner],
    batch_store: ApprovalBatchStore | None,
    shadow_mode: bool,
) -> dict:
    """执行切片:带审批点 → 打包落批次(影子模式只记录)→ interrupt 挂起;决定后从 interrupt 处继续。

    切片级打包(spec #6 D5):actions = 切片意图快照(description + approval_points),
    真实动作级打包在增量 4 业务工具挂接时替换。
    """
    slice_ = Slice(**state["slice"])  # 无损重建:审批断点(approval_points)随切片流转
    if slice_.approval_points:
        if batch_store is None:
            raise RuntimeError("审批批次存储未注入(build_supervisor 需传 batch_store)")
        # 确定性 batch_id(thread+slice_no 的 uuid5):resume 时 Pregel 重放节点,
        # 随机 uuid 会重复落库;切片号在 thread 内唯一且只执行一次,推导稳定。
        batch_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{state.get('thread_id', '')}:{slice_.no}"))
        await batch_store.create_batch(
            batch_id=batch_id,
            thread_id=state.get("thread_id", ""),
            slice_no=slice_.no,
            action_type=slice_.approval_points[0],
            actions=[{"description": slice_.description, "approval_points": slice_.approval_points}],
            mode="shadow" if shadow_mode else "approval",
        )
        if not shadow_mode:
            decision = interrupt(
                {
                    "batch_id": batch_id,
                    "slice_no": slice_.no,
                    "agent": slice_.agent,
                    "description": slice_.description,
                    "approval_points": slice_.approval_points,
                }
            )
            verdict = decision["decision"]
            comment = decision.get("comment")
            await batch_store.decide_batch(batch_id=batch_id, decision=verdict, comment=comment)
            if decision.get("terminate"):
                # 用户终止:批次落 rejected,结果带终止标记(不回流重规划,由 report_terminated 结束)
                return {
                    "results": {slice_.no: {"rejected": True, "terminated": True, "comment": comment or "用户终止"}}
                }
            if verdict == "reject":
                return {"results": {slice_.no: {"rejected": True, "comment": comment}}}
    runner = agents.get(slice_.agent, _default_agent)
    return {"results": {slice_.no: runner(slice_)}}


def _check_layer(state: SupervisorState) -> dict:
    """同层 Send 分支全部完成后执行(join),推进层号。"""
    return {"current_layer": state["current_layer"] + 1}


def _after_check(state: SupervisorState) -> list[Send] | str:
    """本层 join 后:本层有终止 → 终止节点;有被拒 → 回流 manager 重规划;否则推进。

    只检查刚完成的层:重规划后 layers 重算,旧层的拒绝记录不在新 layers 中,不会重复触发。
    """
    layer = state["layers"][state["current_layer"] - 1]
    layer_results = [state.get("results", {}).get(no, {}) for no in layer]
    if any(r.get("terminated") for r in layer_results):
        return "report_terminated"
    if any(r.get("rejected") for r in layer_results):
        return "manager"
    return _next_step(state)


def _report_terminated(state: SupervisorState) -> dict:
    """用户终止(spec #6 D4 terminate 意图):如实结束,不回流重规划。"""
    return {"error": "用户终止任务"}


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
    checkpointer: BaseCheckpointSaver | None = None,
    batch_store: ApprovalBatchStore | None = None,
    shadow_mode: bool = False,
):
    """构建监督图:manager → prepare → 逐层 Send 扇出 → check_layer → … → aggregate。

    checkpointer 为 None 时不持久化(interrupt 会如实报错);生产装配挂 PostgresSaver(spec #6 D1)。
    """
    agents = agents or {}

    async def execute_slice(state: SupervisorState) -> dict:
        return await _execute_slice(state, agents, batch_store, shadow_mode)

    # langgraph 的 StateLike/_Node 泛型上界在静态检查下对具体 TypedDict 与
    # 逆变节点函数必然报 invalid-argument-type(运行时合法且为官方文档模式),故精确忽略。
    builder = StateGraph(SupervisorState)  # ty: ignore
    builder.add_node("manager", _manager_node(planner))  # ty: ignore
    builder.add_node("prepare", _prepare)
    builder.add_node("execute_slice", execute_slice)
    builder.add_node("check_layer", _check_layer)
    builder.add_node("aggregate", _aggregate)
    builder.add_node("report_failure", _report_failure)
    builder.add_node("report_terminated", _report_terminated)

    builder.add_edge(START, "manager")
    builder.add_conditional_edges("manager", _after_manager, {"prepare": "prepare", "fail": "report_failure"})
    builder.add_conditional_edges("prepare", _next_step)
    builder.add_edge("execute_slice", "check_layer")
    builder.add_conditional_edges("check_layer", _after_check)
    builder.add_edge("aggregate", END)
    builder.add_edge("report_failure", END)
    builder.add_edge("report_terminated", END)
    return builder.compile(checkpointer=checkpointer)


def default_supervisor(
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    batch_store: ApprovalBatchStore | None = None,
    shadow_mode: bool = False,
) -> CompiledStateGraph:
    """生产装配入口:真实 ManagerPlanner + 占位业务 Agent(增量 4 换真实子图)。

    checkpointer/batch_store/shadow_mode 由装配方注入(spec #6 D1)。
    """
    return build_supervisor(
        ManagerPlanner(), checkpointer=checkpointer, batch_store=batch_store, shadow_mode=shadow_mode
    )
