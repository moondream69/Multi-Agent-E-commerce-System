"""监督图(术语表:Manager + 业务子图组成的顶层状态图)。

宪章 ADR-0005 + spec #7:
- manager 节点:ManagerPlanner 产出切片计划(或 PlanFailed 强制终止)
- prepare:计算拓扑分层(execution_order)
- 每层经条件边 Send 并行扇出(独立切片并行、依赖串行由分层保证;Pregel 语义下
  同层 Send 分支自动 join 后才推进 check_layer)
- execute_slice:调用业务 Agent 子图(AgentRunner 挂接);切片内收集的审批动作按类型
  打包批次(真实参数快照)→ 切片边界 interrupt(载荷携带本切片全部批次)→ 批准后
  事务内统一执行(apply);影子模式只记录不阻塞;durable 重放以批次表为缓存,不重跑子图
- aggregate/report_failure:汇总结果或如实上报未完成+原因

增量 3 遗留说明:interrupt 的 durable 语义靠监督图 checkpointer + 批次表双保险;
子图不另挂 checkpointer(状态经返回值回传,审批动作已随批次落库)。
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

from python_backend.agents.executor import ApplyFunction, apply_batch_actions
from python_backend.core.approvals import ApprovalBatchRecord, ApprovalBatchStore
from python_backend.core.events import EventEmitter, NullEmitter
from python_backend.core.notifications import emit_notifications
from python_backend.core.planning import ManagerPlanner, PlanFailed, Planner, Slice, SlicePlan
from python_backend.db.audit_store import AuditWriter, NullAuditWriter
from python_backend.db.notification_store import NotificationStore, PostgresNotificationStore
from python_backend.infrastructure.tracing import NullTaskTracer, TaskTracer


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


class BatchPayload(TypedDict):
    """interrupt 载荷中的单个批次(spec #7:一次 interrupt 携带切片全部批次)。

    mode 为登记时持久化的事实(issue #40):恢复分派依据它,而非恢复时的当前剖面。
    """

    batch_id: str
    action_type: str
    actions: list[dict]
    mode: str


def _batch_payload(record: ApprovalBatchRecord) -> BatchPayload:
    """批次行的恢复载荷:mode 随行(恢复分派的事实来源,issue #40)。"""
    return {
        "batch_id": record.batch_id,
        "action_type": record.action_type,
        "actions": record.actions,
        "mode": record.mode,
    }


class SupervisorState(TypedDict, total=False):
    request: str
    thread_id: str
    context: str | None  # 会话记忆上下文(B16:短上下文+摘要,经 create_task 注入)
    plan: SlicePlan | PlanFailed | None
    layers: list[list[int]]
    current_layer: int
    replan_count: int
    results: Annotated[dict[int, dict], merge_dicts]
    error: str | None
    summary: str | None  # 人类可读摘要文本(issue #25:契约 TaskDetail.result.summary 为 string)
    # Send 注入的切片数据(Send 状态为完整替换,execute_slice 经这些 key 取切片)
    slice_no: int
    slice: dict  # 完整切片字段(Slice 构造参数),无损往返
    # 同键随切片下发(#64 B2):Send 是完整替换,执行段看不到主 state,原请求只能这样带过去
    # (由 _next_step 从主 state 的 request 复制)


AgentRunner = Callable[[Slice, str], Awaitable[dict]]

# 重规划回流次数上限(spec #6 D3):超限强制终止,防 LLM 反复产出被拒计划
REPLAN_LIMIT = 2


async def _default_agent(slice_: Slice, task_request: str) -> dict:
    """业务子图占位(装配未挂真实子图时使用):记录执行、返回占位结果。"""
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


def _plan_event_payload(thread_id: str, plan: SlicePlan) -> dict:
    """task.planned 载荷(契约 camelCase):规划轨迹透明——计划产出即广播(含重规划)。"""
    return {
        "threadId": thread_id,
        "slices": [
            {
                "no": s.no,
                "agent": s.agent,
                "description": s.description,
                "dependsOn": s.depends_on,
                "approvalPoints": s.approval_points,
            }
            for s in plan.slices
        ],
    }


def _manager_node(
    planner: Planner, tracer: TaskTracer, emitter: EventEmitter
) -> Callable[[SupervisorState], Awaitable[dict]]:
    async def run(state: SupervisorState) -> dict:
        results = state.get("results", {})
        with tracer.span("manager.plan", input={"request": state["request"]}):
            if not any(r.get("rejected") for r in results.values()):
                context = state.get("context")
                if context:
                    plan: SlicePlan | PlanFailed = await planner.plan(state["request"], context)
                else:
                    plan = await planner.plan(state["request"])
                update: dict = {"plan": plan}
            else:
                # 重规划路径:携拒因 + 已完成上下文;超限强制终止;切片号冲突强制终止
                replan_count = state.get("replan_count", 0) + 1
                if replan_count > REPLAN_LIMIT:
                    return {"plan": PlanFailed(f"重规划次数超限({REPLAN_LIMIT})"), "replan_count": replan_count}
                plan = await planner.plan(_replan_prompt(state["request"], results))
                if isinstance(plan, SlicePlan):
                    conflicts = sorted(no for no in (s.no for s in plan.slices) if no in results)
                    if conflicts:
                        plan = PlanFailed(f"重规划切片号与已完成切片冲突:{conflicts}")
                update = {"plan": plan, "replan_count": replan_count}
            if isinstance(plan, SlicePlan):
                # 协作面板数据源:计划实时可见(不必等任务终态拉详情)
                await emitter.emit("task.planned", _plan_event_payload(state.get("thread_id", ""), plan))
            return update

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
                    # 原请求随切片下发(#64 B2):执行段据此把「全局任务」摆在切片职责之前——
                    # 描述缺主语时(规划器漏写对象),执行段不再只能从字面猜
                    "request": state.get("request", ""),
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
    apply_fn: ApplyFunction,
    emitter: EventEmitter,
    notification_store: NotificationStore,
    tracer: TaskTracer,
    audit: AuditWriter,
) -> dict:
    """执行切片(spec #7):子图运行 → 审批动作按类型打包(真实参数快照)→ 边界 interrupt → apply。

    durable 重放防护:批次表即子图结果的 durable 记录——重放时若 (thread, slice_no) 已有批次,
    跳过子图重跑(LLM 轮次与 auto 工具副作用不重复),直接以既有批次继续中断/决定流程。
    批次打包按 action_type 分组(同类型同批、不跨类型混批,B4);
    batch_id = uuid5(thread:slice:action_type) 确定性推导;一次 interrupt 携带本切片全部批次;
    批准批次在 resume 后事务内统一执行(apply,批内同进同退),冲突如实上报(B18)。
    """
    slice_ = Slice(**state["slice"])  # 无损重建:审批断点(approval_points)随切片流转
    thread_id = state.get("thread_id", "")
    existing = await batch_store.list_by_slice(thread_id, slice_.no) if batch_store is not None else []
    batches: list[BatchPayload] = []

    if existing:
        # durable 重放:子图不重跑(LLM 轮次与 auto 工具副作用不重复),输出从批次行恢复
        run = existing[0].run_output or {}
        batches = [_batch_payload(r) for r in existing]
    else:
        # 协作面板数据源:分派信号(durable 重放不重发,避免 resume 时闪现「执行中」)
        await emitter.emit("slice.started", {"threadId": thread_id, "sliceNo": slice_.no, "agent": slice_.agent})
        runner = agents.get(slice_.agent, _default_agent)
        with tracer.span(f"slice.{slice_.agent}", input={"description": slice_.description}):
            try:
                # 第二参数 = 原请求(#64 B2):执行段据此组装「全局任务 + 本切片职责」
                run = await runner(slice_, state.get("request", ""))
            except Exception:
                await audit.record(
                    thread_id=thread_id,
                    agent_id=slice_.agent,
                    type_="slice",
                    status="failed",
                    input={"description": slice_.description},
                )
                raise
        run_output = {
            "answer": run.get("answer"),
            "executed": run.get("executed"),
            "incomplete": run.get("incomplete"),  # durable 重放须恢复未完成语义(与审计同一份记录)
            # issue #51:引用条目随答案下发(经批次 run_output 与任务详情暴露给前端)
            "citations": run.get("citations"),
            # issue #69:系统记录类查证结果(判分材料据此核商品/订单事实;durable 重放须一并恢复)
            "evidence": run.get("evidence"),
        }
        await audit.record(
            thread_id=thread_id,
            agent_id=slice_.agent,
            type_="slice",
            # 未完成切片(步数超限/子图 LLM 失败)如实记 failed,不得因捕获而误记为 completed
            status="failed" if run.get("incomplete") else "completed",
            input={"description": slice_.description},
            output=run_output,
        )
        actions = run.get("actions") or []
        if actions:
            if batch_store is None:
                raise RuntimeError("审批批次存储未注入(build_supervisor 需传 batch_store)")
            groups: dict[str, list[dict]] = {}
            for item in actions:
                groups.setdefault(item["action"], []).append(item)
            for action_type in sorted(groups):  # 排序:重放时批次序确定
                batch_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{thread_id}:{slice_.no}:{action_type}"))
                record = await batch_store.create_batch(
                    batch_id=batch_id,
                    thread_id=thread_id,
                    slice_no=slice_.no,
                    action_type=action_type,
                    actions=groups[action_type],
                    mode="shadow" if shadow_mode else "approval",
                    run_output=run_output,
                )
                batches.append(
                    # mode 取持久化记录(create 幂等:已存在行时返回库中值,重放不覆盖)
                    _batch_payload(record)
                )
            if batches:
                # WS 载荷按契约 camelCase(与 REST 序列化一致);interrupt 载荷保持 snake(内部)
                await emitter.emit(
                    "approval.requested",
                    {
                        "threadId": thread_id,
                        "sliceNo": slice_.no,
                        "agent": slice_.agent,
                        "batches": [
                            {"batchId": b["batch_id"], "actionType": b["action_type"], "actions": b["actions"]}
                            for b in batches
                        ],
                    },
                )
                tracer.record_event(
                    "approval.requested",
                    {"threadId": thread_id, "sliceNo": slice_.no, "batchIds": [b["batch_id"] for b in batches]},
                )

    merged: dict = {"agent": slice_.agent, "description": slice_.description}
    if run.get("answer"):
        merged["answer"] = run["answer"]
    if run.get("executed"):
        merged["executed"] = True
    if run.get("citations"):
        merged["citations"] = run["citations"]  # issue #51:与答案同份下发(get_task 的 results)
    if run.get("evidence"):
        # issue #69:系统记录类查证结果随切片产物下发(跑批器经 GET /api/tasks 读它落快照)
        merged["evidence"] = run["evidence"]
    if run.get("incomplete"):
        # 未完成如实上报(B17 步数超限 / issue #10 子图 LLM 失败),由 _aggregate 转 error
        merged["incomplete"] = run["incomplete"]

    async def _emit_completed() -> None:
        """切片终态广播(rejected > failed > completed):面板据此归位,防卡「执行中」。"""
        if merged.get("rejected"):
            status = "rejected"
        elif merged.get("incomplete"):
            status = "failed"
        else:
            status = "completed"
        await emitter.emit(
            "slice.completed",
            {"threadId": thread_id, "sliceNo": slice_.no, "agent": slice_.agent, "status": status},
        )

    if batches:
        assert batch_store is not None  # 有批次必有存储(创建/重放路径都经 store)
        # 分派依据 = 批次持久化 mode(登记时事实),而非当前剖面(issue #40):跨剖面恢复
        # (如生产挂起后切演练 resume)时,当前 shadow_mode 不得先于 interrupt 吞掉人工决定
        approval_batches = [batch for batch in batches if batch["mode"] == "approval"]
        shadow_count = len(batches) - len(approval_batches)
        if shadow_count:
            merged["shadow_batches"] = shadow_count
        if not approval_batches:
            await _emit_completed()
            return {"results": {slice_.no: merged}}

        decision = interrupt(
            {
                "slice_no": slice_.no,
                "agent": slice_.agent,
                "description": slice_.description,
                "batches": approval_batches,
            }
        )
        # issue #41:决定人随 resume 载荷透传(端点解 JWT 用户名),落 decided_by 审计列
        decided_by = decision.get("decided_by")
        if decision.get("terminate"):
            comments = [d.get("comment") for d in (decision.get("decisions") or {}).values() if d.get("comment")]
            comment = "; ".join(comments) if comments else "用户终止"
            for batch in approval_batches:
                await batch_store.decide_batch(
                    batch_id=batch["batch_id"],
                    decision="reject",
                    comment=comment,
                    decided_by=decided_by,
                )
            merged.update({"rejected": True, "terminated": True, "comment": comment})
            await _emit_completed()
            return {"results": {slice_.no: merged}}

        decisions: dict = decision.get("decisions") or {}
        rejected_comments: list[str] = []
        conflicts: list[str] = []
        for batch in approval_batches:
            batch_id = batch["batch_id"]
            decided: dict[str, str | None] = decisions.get(batch_id) or {"decision": "reject", "comment": None}
            decision_value = decided["decision"] or "reject"
            comment_value = decided.get("comment")
            await batch_store.decide_batch(
                batch_id=batch_id, decision=decision_value, comment=comment_value, decided_by=decided_by
            )
            tracer.record_event(
                "approval.decided",
                {"threadId": thread_id, "batchId": batch_id, "decision": decision_value, "comment": comment_value},
            )
            if decision_value == "approve":
                outcome = await apply_fn(batch_id, batch["actions"])
                if not outcome.applied:
                    conflicts.append(f"{batch['action_type']}:{outcome.reason}")
                else:
                    # 提交后组装通知(spec #9 A8/A9;增量 8-T1 起同时落库):效果已落库才广播,回滚不误报
                    await emit_notifications(notification_store, emitter, outcome.effects)
            else:
                rejected_comments.append(comment_value or "")
        if rejected_comments:
            merged["rejected"] = True
            merged["comment"] = "; ".join(c for c in rejected_comments if c)
        if conflicts:
            merged["conflict"] = "; ".join(conflicts)
    await _emit_completed()
    return {"results": {slice_.no: merged}}


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
    """汇总(监督图定义:规划 → 分派 → 汇总):产人类可读摘要供上层/前端消费(issue #25)。

    摘要 = 「完成 M/N 个切片」(M=最终计划内已产出结果的切片数,N=计划切片数),零 LLM 纯拼装;
    切片结构化细节由 `plan` 与 `results` 状态各自承载,不进摘要(契约 summary 为 string)。

    冲突/未完成如实上报为 error(B18/B17):审批动作执行冲突与子图步数超限不是静默事件。
    """
    plan = state["plan"]
    if isinstance(plan, SlicePlan):
        results = state.get("results", {})
        problems = []
        for no in sorted(results):
            result = results[no]
            if result.get("conflict"):
                problems.append(f"切片 {no} 审批动作执行冲突:{result['conflict']}")
            if result.get("incomplete"):
                problems.append(f"切片 {no} 未完成:{result['incomplete']}")
        # M 只数最终计划内切片:重规划回流跨轮累计 results(含被拒切片),按总数计会产出「完成 2/1」式失真
        done = sum(1 for s in plan.slices if s.no in results)
        return {
            "error": state.get("error") or ("; ".join(problems) if problems else None),
            "summary": f"完成 {done}/{len(plan.slices)} 个切片",
        }
    return {"error": state.get("error"), "summary": "完成 0/0 个切片"}


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
    apply_fn: ApplyFunction | None = None,
    emitter: EventEmitter | None = None,
    notification_store: NotificationStore | None = None,
    tracer: TaskTracer | None = None,
    audit: AuditWriter | None = None,
):
    """构建监督图:manager → prepare → 逐层 Send 扇出 → check_layer → … → aggregate。

    checkpointer 为 None 时不持久化(interrupt 会如实报错);生产装配挂 PostgresSaver(spec #6 D1)。
    apply_fn/emitter/notification_store/tracer/audit 默认生产实现或 no-op(spec #7/#8/增量 8 接缝),
    测试注入假实现。
    """
    agents = agents or {}
    apply_fn = apply_fn or apply_batch_actions
    emitter = emitter or NullEmitter()
    notification_store = notification_store or PostgresNotificationStore()
    tracer = tracer or NullTaskTracer()
    audit = audit or NullAuditWriter()

    async def execute_slice(state: SupervisorState) -> dict:
        return await _execute_slice(
            state, agents, batch_store, shadow_mode, apply_fn, emitter, notification_store, tracer, audit
        )

    # langgraph 的 StateLike/_Node 泛型上界在静态检查下对具体 TypedDict 与
    # 逆变节点函数必然报 invalid-argument-type(运行时合法且为官方文档模式),故精确忽略。
    builder = StateGraph(SupervisorState)  # ty: ignore
    builder.add_node("manager", _manager_node(planner, tracer, emitter))  # ty: ignore
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
    agents: dict[str, AgentRunner] | None = None,
    emitter: EventEmitter | None = None,
    notification_store: NotificationStore | None = None,
    tracer: TaskTracer | None = None,
    audit: AuditWriter | None = None,
) -> CompiledStateGraph:
    """生产装配入口:真实 ManagerPlanner + 注入的业务子图 runner(spec #7 挂接)。

    checkpointer/batch_store/shadow_mode/emitter/notification_store/tracer/audit 由装配方注入
    (spec #6 D1 / #7 / #8 / 增量 8)。
    """
    return build_supervisor(
        ManagerPlanner(),
        agents=agents,
        checkpointer=checkpointer,
        batch_store=batch_store,
        shadow_mode=shadow_mode,
        emitter=emitter,
        notification_store=notification_store,
        tracer=tracer,
        audit=audit,
    )
