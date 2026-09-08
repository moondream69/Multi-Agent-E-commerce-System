"""REST API(spec #6 D4 + spec #7):切片式人工环节端点。

- POST /api/tasks:发起任务(生成 thread_id,图跑至中断/完成)
- GET  /api/approvals:全量未决批次(pending + shadow,审批中心数据源)
- GET  /api/threads/{thread_id}/approvals:该 thread 挂起批次列表
- POST /api/threads/{thread_id}/resume:结构化决定(按钮入口,多批一次提交)
- POST /api/threads/{thread_id}/message:自然消息入口(关键词判定决定意图)
- POST /api/threads/{thread_id}/shadow-batches/{batch_id}/execute:影子批次补执行(A15)

WS 实时通道与审批中心 UI 见 api/ws(spec #7)。
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel

from python_backend.agents.executor import ApplyFunction, apply_batch_actions
from python_backend.core.approvals import ApprovalBatchStore, parse_decision_intent
from python_backend.core.events import EventEmitter, NullEmitter
from python_backend.core.graph import SupervisorState
from python_backend.infrastructure.tracing import NullTaskTracer, TaskTracer


class TaskCreateRequest(BaseModel):
    request: str


class ResumeDecision(BaseModel):
    decision: Literal["approve", "reject"]  # 拼写错误由 pydantic 422 拦截,不落误决定
    comment: str | None = None


class MessageRequest(BaseModel):
    text: str


def _config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


async def _pending_interrupts(graph: CompiledStateGraph, thread_id: str) -> list[tuple[str, dict]]:
    """挂起中断的 (id, value) 配对——稳定 API(aget_state.tasks.interrupts)发现,零实验 API 依赖。"""
    snapshot = await graph.aget_state(_config(thread_id))
    return [(t.interrupts[0].id, t.interrupts[0].value) for t in snapshot.tasks if t.interrupts]


async def _resume(
    graph: CompiledStateGraph,
    resume_map: dict[str, dict],
    thread_id: str,
    emitter: EventEmitter,
    tracer: TaskTracer,
) -> dict:
    """组装好的 resume_map 驱动图恢复,统一响应形状;完成后广播任务终态事件。"""
    with tracer.trace(thread_id):
        result = await graph.ainvoke(Command(resume=resume_map), _config(thread_id))
    if "__interrupt__" in result:
        # 下一层切片又挂起:如实广播(多层切片任务不只一次中断)
        await emitter.emit("task.interrupted", {"threadId": thread_id, "status": "interrupted"})
        return {"status": "interrupted"}
    status = "failed" if result.get("error") else "completed"
    await emitter.emit(f"task.{status}", {"threadId": thread_id, "status": status, "error": result.get("error")})
    return {"status": "completed", "error": result.get("error"), "summary": result.get("summary")}


def _serialize_batch(record) -> dict:
    """批次序列化(驼峰,与契约 events.ts ApprovalBatch 字段一一对应)。

    旧形状(增量 3 意图快照)防御性兜底:action 取 "?",不因历史行 500。
    """
    return {
        "batchId": record.batch_id,
        "threadId": record.thread_id,
        "sliceNo": record.slice_no,
        "actionType": record.action_type,
        "actions": [
            {
                "action": action.get("action", "?"),
                "params": action.get("params"),
                "snapshot": action.get("snapshot"),
            }
            for action in record.actions
        ],
        "status": record.status,
        "mode": record.mode,
        "comment": record.comment,
        "result": record.result,
        "runOutput": record.run_output,
    }


def _interrupt_batch_ids(value: dict) -> list[str]:
    """interrupt 载荷中的全部批次 id(spec #7:一次 interrupt 携带切片全部批次)。"""
    return [batch["batch_id"] for batch in value.get("batches", [])]


def create_app(
    *,
    graph: CompiledStateGraph | None = None,
    batch_store: ApprovalBatchStore | None = None,
    apply_fn: ApplyFunction | None = None,
    emitter: EventEmitter | None = None,
    tracer: TaskTracer | None = None,
) -> FastAPI:
    """构建 API 应用:graph/batch_store/apply_fn/emitter/tracer 可注入(测试)或由 lifespan 装配(生产)。

    apply_fn 默认真实 apply_batch_actions;emitter/tracer 默认 no-op(spec #7 接缝)。
    """
    app = FastAPI(title="Multi-Agent E-commerce System(切片式人工环节)", version="0.1.0")
    app.state.graph = graph
    app.state.batch_store = batch_store
    app.state.apply_fn = apply_fn or apply_batch_actions
    app.state.emitter = emitter or NullEmitter()
    app.state.tracer = tracer or NullTaskTracer()

    @app.post("/api/tasks", status_code=201)
    async def create_task(body: TaskCreateRequest) -> dict:
        thread_id = str(uuid.uuid4())
        await app.state.emitter.emit("task.created", {"threadId": thread_id, "status": "created"})
        with app.state.tracer.trace(thread_id):
            result = await app.state.graph.ainvoke(
                SupervisorState(request=body.request, thread_id=thread_id), _config(thread_id)
            )
        if "__interrupt__" in result:
            await app.state.emitter.emit("task.interrupted", {"threadId": thread_id, "status": "interrupted"})
            return {"thread_id": thread_id, "status": "interrupted"}
        status = "failed" if result.get("error") else "completed"
        await app.state.emitter.emit(
            f"task.{status}", {"threadId": thread_id, "status": status, "error": result.get("error")}
        )
        return {
            "thread_id": thread_id,
            "status": "completed",
            "error": result.get("error"),
            "summary": result.get("summary"),
        }

    @app.get("/api/approvals")
    async def list_all_open() -> dict:
        """全量未决批次(pending + shadow):审批中心数据源(spec #7 用户故事 5/6)。"""
        batches = await app.state.batch_store.list_open()
        return {"approvals": [_serialize_batch(batch) for batch in batches]}

    @app.get("/api/threads/{thread_id}/approvals")
    async def list_approvals(thread_id: str) -> dict:
        batches = await app.state.batch_store.list_pending(thread_id)
        return {"approvals": [_serialize_batch(batch) for batch in batches]}

    @app.post("/api/threads/{thread_id}/resume")
    async def resume_thread(thread_id: str, body: dict[str, ResumeDecision]) -> dict:
        """按钮入口:body = {batch_id: {decision, comment}},多批一次提交(逐批决定)。"""
        pending = await _pending_interrupts(app.state.graph, thread_id)
        if not pending:
            raise HTTPException(status_code=409, detail="当前无挂起审批")

        all_batch_ids = {batch_id for _iid, value in pending for batch_id in _interrupt_batch_ids(value)}
        for batch_id in body:
            if batch_id not in all_batch_ids:
                raise HTTPException(status_code=404, detail=f"批次 {batch_id} 未挂起或不存在")
        if set(body) != all_batch_ids:
            raise HTTPException(
                status_code=422,
                detail=f"请对全部 {len(all_batch_ids)} 个挂起批次作出决定(一次提交,缺一不可)",
            )
        by_interrupt: dict[str, dict[str, dict]] = {}
        for batch_id, decision in body.items():
            for interrupt_id, value in pending:
                if batch_id in _interrupt_batch_ids(value):
                    by_interrupt.setdefault(interrupt_id, {})[batch_id] = {
                        "decision": decision.decision,
                        "comment": decision.comment,
                    }
                    break

        resume_map = {iid: {"terminate": False, "decisions": decisions} for iid, decisions in by_interrupt.items()}
        for decisions in by_interrupt.values():
            for batch_id, decided in decisions.items():
                await app.state.emitter.emit(
                    "approval.decided",
                    {
                        "threadId": thread_id,
                        "batchId": batch_id,
                        "decision": decided["decision"],
                        "comment": decided["comment"],
                    },
                )
        return await _resume(app.state.graph, resume_map, thread_id, app.state.emitter, app.state.tracer)

    @app.post("/api/threads/{thread_id}/message")
    async def post_message(thread_id: str, body: MessageRequest) -> dict:
        intent = parse_decision_intent(body.text)
        if intent is None:
            raise HTTPException(status_code=422, detail="无法识别决定意图(支持:同意/批准/通过、拒绝/驳回、终止/取消)")

        pending = await _pending_interrupts(app.state.graph, thread_id)
        if not pending:
            raise HTTPException(status_code=409, detail="当前无挂起审批")

        # terminate:全部挂起批次落 rejected + 终止标记(图不回流重规划,直接「用户终止」结束)
        terminate = intent == "terminate"
        decision = "reject" if terminate else intent
        resume_map = {
            interrupt_id: {
                "terminate": terminate,
                "decisions": {
                    batch_id: {"decision": decision, "comment": body.text} for batch_id in _interrupt_batch_ids(value)
                },
            }
            for interrupt_id, value in pending
        }
        for _interrupt_id, value in pending:
            for batch_id in _interrupt_batch_ids(value):
                await app.state.emitter.emit(
                    "approval.decided",
                    {"threadId": thread_id, "batchId": batch_id, "decision": decision, "comment": body.text},
                )
        return await _resume(app.state.graph, resume_map, thread_id, app.state.emitter, app.state.tracer)

    @app.post("/api/threads/{thread_id}/shadow-batches/{batch_id}/execute")
    async def execute_shadow_batch(thread_id: str, batch_id: str) -> dict:
        """影子批次补执行(A15):演练环境高危建议由人工一键补执行。"""
        record = await app.state.batch_store.get_batch(batch_id=batch_id)
        if record is None or record.thread_id != thread_id:
            raise HTTPException(status_code=404, detail=f"批次 {batch_id} 不存在")
        if record.mode != "shadow":
            raise HTTPException(status_code=400, detail="仅影子批次可补执行")
        if record.status == "executed":
            return {"status": "executed", "result": record.result}
        outcome = await app.state.apply_fn(batch_id, record.actions)
        if not outcome.applied:
            raise HTTPException(status_code=409, detail=outcome.reason)
        return {"status": "executed", "result": {"applied": True}}

    return app
