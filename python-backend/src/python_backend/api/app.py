"""REST API(spec #6 D4):切片式人工环节的最小四端点。

- POST /api/tasks:发起任务(生成 thread_id,图跑至中断/完成)
- GET  /api/threads/{thread_id}/approvals:挂起批次列表(审批中心数据源)
- POST /api/threads/{thread_id}/resume:结构化决定(按钮入口,即时逐批 resume)
- POST /api/threads/{thread_id}/message:自然消息入口(关键词判定决定意图)

WS 实时通道与审批中心 UI 留增量 4/5(本增量只钉 REST 契约与事件形状)。
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel

from python_backend.core.approvals import ApprovalBatchStore, parse_decision_intent
from python_backend.core.graph import SupervisorState


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


async def _resume(graph: CompiledStateGraph, resume_map: dict[str, dict], thread_id: str) -> dict:
    """组装好的 resume_map 驱动图恢复,统一响应形状。"""
    result = await graph.ainvoke(Command(resume=resume_map), _config(thread_id))
    if "__interrupt__" in result:
        return {"status": "interrupted"}
    return {"status": "completed", "error": result.get("error"), "summary": result.get("summary")}


def _serialize_batch(record) -> dict:
    """批次序列化(驼峰,与契约 events.ts ApprovalBatch 字段一一对应)。"""
    return {
        "batchId": record.batch_id,
        "threadId": record.thread_id,
        "sliceNo": record.slice_no,
        "actionType": record.action_type,
        "actions": [
            {"description": action["description"], "approvalPoints": action["approval_points"]}
            for action in record.actions
        ],
        "status": record.status,
        "mode": record.mode,
        "comment": record.comment,
    }


def create_app(*, graph: CompiledStateGraph | None = None, batch_store: ApprovalBatchStore | None = None) -> FastAPI:
    """构建 API 应用:graph/batch_store 可注入(测试)或由 lifespan 装配后写入 app.state(生产)。"""
    app = FastAPI(title="Multi-Agent E-commerce System(切片式人工环节)", version="0.1.0")
    app.state.graph = graph
    app.state.batch_store = batch_store

    @app.post("/api/tasks", status_code=201)
    async def create_task(body: TaskCreateRequest) -> dict:
        thread_id = str(uuid.uuid4())
        result = await app.state.graph.ainvoke(
            SupervisorState(request=body.request, thread_id=thread_id), _config(thread_id)
        )
        if "__interrupt__" in result:
            return {"thread_id": thread_id, "status": "interrupted"}
        return {
            "thread_id": thread_id,
            "status": "completed",
            "error": result.get("error"),
            "summary": result.get("summary"),
        }

    @app.get("/api/threads/{thread_id}/approvals")
    async def list_approvals(thread_id: str) -> dict:
        batches = await app.state.batch_store.list_pending(thread_id)
        return {"approvals": [_serialize_batch(batch) for batch in batches]}

    @app.post("/api/threads/{thread_id}/resume")
    async def resume_thread(thread_id: str, body: dict[str, ResumeDecision]) -> dict:
        pending = await _pending_interrupts(app.state.graph, thread_id)

        resume_map: dict[str, dict] = {}
        for batch_id, decision in body.items():
            matched = next((iid for iid, value in pending if value.get("batch_id") == batch_id), None)
            if matched is None:
                raise HTTPException(status_code=404, detail=f"批次 {batch_id} 未挂起或不存在")
            resume_map[matched] = {"decision": decision.decision, "comment": decision.comment}

        return await _resume(app.state.graph, resume_map, thread_id)

    @app.post("/api/threads/{thread_id}/message")
    async def post_message(thread_id: str, body: MessageRequest) -> dict:
        intent = parse_decision_intent(body.text)
        if intent is None:
            raise HTTPException(status_code=422, detail="无法识别决定意图(支持:同意/批准/通过、拒绝/驳回、终止/取消)")

        pending = await _pending_interrupts(app.state.graph, thread_id)
        if not pending:
            raise HTTPException(status_code=409, detail="当前无挂起审批")

        # terminate:全部挂起批次落 rejected + 终止标记(图不回流重规划,直接「用户终止」结束)
        resume_map = {
            iid: {
                "decision": "reject",
                "terminate": intent == "terminate",
                "comment": body.text,
            }
            for iid, _value in pending
        }
        return await _resume(app.state.graph, resume_map, thread_id)

    return app
