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
from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy import select

from python_backend.agents.executor import (
    ApplyFunction,
    FxProvider,
    InsufficientStockError,
    OrderCreationError,
    apply_batch_actions,
    create_order_with_stock,
    default_fx,
)
from python_backend.agents.registry import REGISTRY
from python_backend.core.approvals import ApprovalBatchStore, parse_decision_intent
from python_backend.core.auth import create_token, decode_token, verify_password
from python_backend.core.drafting import DraftingError, DraftingService
from python_backend.core.events import EventEmitter, NullEmitter
from python_backend.core.graph import SupervisorState
from python_backend.core.imports import (
    CsvFormatError,
    import_orders,
    import_products,
    parse_orders_csv,
    parse_products_csv,
)
from python_backend.core.memory import PostgresSessionMemory, SessionMemory
from python_backend.core.notifications import emit_notifications
from python_backend.db.audit_store import AuditWriter, NullAuditWriter
from python_backend.db.conversation_store import delete_conversation, list_conversations, session_has_pending_batches
from python_backend.db.models import Product, User
from python_backend.db.session import SessionFactory
from python_backend.db.task_store import create_task_row, get_task, list_tasks, task_session, update_task_row
from python_backend.infrastructure.tracing import NullTaskTracer, TaskTracer


class TaskCreateRequest(BaseModel):
    request: str
    session_id: str | None = None  # B16:会话归属(默认 default;A2 多会话 UI 留增量 6)


class OrderCreateRequest(BaseModel):
    """REST 下单(spec #8 B9):买家侧模拟入口,直接入口免审批护栏(信任输入,不经 LLM)。"""

    product_id: int
    customer_id: int | None = None
    total_amount: Decimal
    currency: str = "USD"
    platform: str | None = None
    reference: str | None = None


class ResumeDecision(BaseModel):
    decision: Literal["approve", "reject"]  # 拼写错误由 pydantic 422 拦截,不落误决定
    comment: str | None = None


class MessageRequest(BaseModel):
    text: str


class LoginRequest(BaseModel):
    username: str
    password: str


class DraftingRequest(BaseModel):
    """起草工作台请求(spec #8 B11/B19):买家消息 + 目标语言 + 可选订单号(查证)。"""

    message: str
    locale: str = "zh"
    order_id: int | None = None


async def _authenticate(body: LoginRequest) -> dict:
    """登录校验:bcrypt 比对 → 签发 JWT。凭据错误/用户不存在 → 401(不区分,防探测)。"""
    async with SessionFactory() as session:
        user = (await session.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"token": create_token(user.username, user.id), "username": user.username}


def _current_user_id(request: Request) -> int | None:
    """从 Authorization 头解出用户 id;无 token/未开认证时 None(记忆不落库)。"""
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip()
    if not token:
        return None
    payload = decode_token(token)
    return payload["uid"] if payload else None


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
    memory: SessionMemory | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
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
    # 图状态为最新规划(含重规划):刷新任务行切片计划,驾驶舱时间线展示重规划后的段
    snapshot = await graph.aget_state(_config(thread_id))
    refreshed_plan = _plan_payload(snapshot.values.get("plan")) if snapshot.values else None
    await update_task_row(
        thread_id=thread_id,
        status=status,
        slice_plan=refreshed_plan,
        result={"summary": result.get("summary"), "error": result.get("error")},
    )
    if memory is not None and session_id and (result.get("summary") or result.get("error")):
        await memory.record(
            session_id,
            user_id,
            role="assistant",
            content=str(result.get("summary") or result.get("error")),
            task_id=thread_id,
        )
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


def _product_payload(product: Product) -> dict:
    """商品序列化(驼峰,与契约 events.ts ProductListItem 字段一一对应)。"""
    return {
        "id": product.id,
        "sku": product.sku,
        "title": product.title,
        "price": str(product.price),
        "currency": product.currency,
        "category": product.category,
        "status": product.status.value,
        "stock": product.stock,
        "alertThreshold": product.alert_threshold,
    }


def _interrupt_batch_ids(value: dict) -> list[str]:
    """interrupt 载荷中的全部批次 id(spec #7:一次 interrupt 携带切片全部批次)。"""
    return [batch["batch_id"] for batch in value.get("batches", [])]


def _plan_payload(plan) -> dict | None:
    """SlicePlan → 任务行切片计划载荷(tasks.slice_plan / 详情响应数据源)。"""
    slices = getattr(plan, "slices", None)
    if slices is None:
        return None
    return {
        "slices": [
            {
                "no": slice_.no,
                "agent": slice_.agent,
                "description": slice_.description,
                "depends_on": slice_.depends_on,
                "approval_points": slice_.approval_points,
            }
            for slice_ in slices
        ]
    }


async def _csv_text(request: Request) -> str:
    """CSV 请求体解码(UTF-8,容忍 BOM):编码不符 400 拒绝,不落任何行。"""
    try:
        return (await request.body()).decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=400, detail="CSV 编码须为 UTF-8(UTF-8 BOM 可接受)") from error


def create_app(
    *,
    graph: CompiledStateGraph | None = None,
    batch_store: ApprovalBatchStore | None = None,
    apply_fn: ApplyFunction | None = None,
    emitter: EventEmitter | None = None,
    tracer: TaskTracer | None = None,
    fx_service: FxProvider | None = None,
    auth_required: bool = True,
    memory: SessionMemory | None = None,
    drafting: DraftingService | None = None,
    audit: AuditWriter | None = None,
) -> FastAPI:
    """构建 API 应用:graph/batch_store/apply_fn/emitter/tracer/fx_service/memory/drafting/audit
    可注入(测试)或由 lifespan 装配(生产)。

    apply_fn 默认真实 apply_batch_actions;emitter/tracer/audit 默认 no-op(spec #7/#8 接缝);
    fx_service 默认由下单端点惰性解析(spec #8 接缝,测试注入假实现);
    auth_required 默认开(spec #8 A1 全门禁),非认证行为的测试显式关闭;
    memory 默认 PG 会话记忆(spec #8 B16 接缝,测试注入内存实现);
    drafting 默认真实起草服务(spec #8 B11 接缝,测试注入假实现)。
    """
    app = FastAPI(title="Multi-Agent E-commerce System(切片式人工环节)", version="0.1.0")
    app.state.graph = graph
    app.state.batch_store = batch_store
    app.state.apply_fn = apply_fn or apply_batch_actions
    app.state.emitter = emitter or NullEmitter()
    app.state.tracer = tracer or NullTaskTracer()
    app.state.auth_enabled = auth_required
    app.state.fx_service = fx_service  # None 时由下单端点惰性解析(default_fx 需运行中事件循环)
    app.state.memory = memory or PostgresSessionMemory()
    app.state.drafting = drafting or DraftingService()
    app.state.audit = audit or NullAuditWriter()

    @app.middleware("http")
    async def auth_gate(request: Request, call_next):
        """A1 全门禁:除 /api/auth/login 与 /health 外,/api 一律要求有效 Bearer token(401)。"""
        path = request.url.path
        if app.state.auth_enabled and path.startswith("/api") and path != "/api/auth/login":
            header = request.headers.get("authorization", "")
            token = header.removeprefix("Bearer ").strip()
            if not token or decode_token(token) is None:
                return JSONResponse(status_code=401, content={"detail": "未认证或令牌无效"})
        return await call_next(request)

    @app.post("/api/auth/login")
    async def login(body: LoginRequest) -> dict:
        return await _authenticate(body)

    @app.post("/api/tasks", status_code=201)
    async def create_task(body: TaskCreateRequest, request: Request) -> dict:
        thread_id = str(uuid.uuid4())
        session_id = body.session_id or "default"
        user_id = _current_user_id(request)
        # B16 会话记忆:携历史上下文入图;用户消息落库(任务行同落,驾驶舱数据源)
        context = await app.state.memory.get_context(session_id, user_id)
        await app.state.memory.record(session_id, user_id, role="user", content=body.request, task_id=thread_id)
        await create_task_row(
            thread_id=thread_id, user_id=user_id, session_id=session_id, type_="chat", request=body.request
        )
        await app.state.emitter.emit("task.created", {"threadId": thread_id, "status": "created"})
        with app.state.tracer.trace(thread_id):
            result = await app.state.graph.ainvoke(
                SupervisorState(request=body.request, thread_id=thread_id, context=context), _config(thread_id)
            )
        if "__interrupt__" in result:
            await update_task_row(thread_id=thread_id, status="interrupted")
            await app.state.emitter.emit("task.interrupted", {"threadId": thread_id, "status": "interrupted"})
            return {"thread_id": thread_id, "status": "interrupted"}
        status = "failed" if result.get("error") else "completed"
        await update_task_row(
            thread_id=thread_id,
            status=status,
            slice_plan=_plan_payload(result.get("plan")),
            result={"summary": result.get("summary"), "error": result.get("error")},
        )
        await app.state.emitter.emit(
            f"task.{status}", {"threadId": thread_id, "status": status, "error": result.get("error")}
        )
        if result.get("summary") or result.get("error"):
            await app.state.memory.record(
                session_id,
                user_id,
                role="assistant",
                content=str(result.get("summary") or result.get("error")),
                task_id=thread_id,
            )
        return {
            "thread_id": thread_id,
            "status": status,
            "error": result.get("error"),
            "summary": result.get("summary"),
        }

    @app.post("/api/drafting")
    async def drafting(body: DraftingRequest) -> dict:
        """起草工作台(spec #8 B11/B19):查证优先 → 多语草稿,不落库/不进审批流。"""
        try:
            return await app.state.drafting.draft(message=body.message, locale=body.locale, order_id=body.order_id)
        except DraftingError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/orders", status_code=201)
    async def create_order(body: OrderCreateRequest) -> dict:
        """买家侧模拟下单(spec #8 B9):扣真实库存(负数防护),汇率快照随订单落库。

        直接入口(不经 LLM)免审批护栏;库存不足 409。汇率不可用时不再拒单(spec #9):
        fx_rate 留空落库 + fx_missing 通知(人工可见待核)。
        """
        try:
            result = await create_order_with_stock(
                product_id=body.product_id,
                customer_id=body.customer_id,
                total_amount=body.total_amount,
                currency=body.currency,
                platform=body.platform,
                reference=body.reference,
                fx_service=app.state.fx_service or default_fx(),
            )
        except InsufficientStockError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except OrderCreationError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        # 提交后组装通知(spec #9 A8/A9):订单创建 + 汇率缺失 + 库存跌破阈值
        await emit_notifications(app.state.emitter, result.effects)
        return {"order": result.order}

    @app.post("/api/import/products")
    async def import_products_csv(request: Request) -> dict:
        """商品 CSV 导入(spec #8 B8):sku 幂等,落 draft;行级报告 {created, skipped, errors}。"""
        text = await _csv_text(request)
        try:
            rows, parse_errors = parse_products_csv(text)
        except CsvFormatError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        report = await import_products(rows)
        report.errors.extend(parse_errors)
        return {"report": report.as_dict()}

    @app.post("/api/import/orders")
    async def import_orders_csv(request: Request) -> dict:
        """订单 CSV 导入(spec #8 B8):reference 幂等,按 SKU 定位商品;历史入口不扣库存不取快照。"""
        text = await _csv_text(request)
        try:
            rows, parse_errors = parse_orders_csv(text)
        except CsvFormatError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        report = await import_orders(rows)
        report.errors.extend(parse_errors)
        return {"report": report.as_dict()}

    @app.get("/api/tasks")
    async def list_task_rows(session_id: str | None = None) -> dict:
        """任务列表(驾驶舱数据源,spec #8):最新在前,标题取请求前 20 字(A2 截断语义)。

        session_id 给定时只返回该会话的任务(spec #9 A2:切换会话即切换历史视图)。
        """
        rows = await list_tasks(session_id=session_id)
        return {
            "tasks": [
                {
                    "threadId": row.thread_id,
                    "sessionId": row.session_id,
                    "type": row.type,
                    "status": row.status.value,
                    "title": ((row.input or {}).get("request") or "")[:20],
                    "createdAt": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
        }

    @app.get("/api/tasks/{thread_id}")
    async def get_task_detail(thread_id: str) -> dict:
        """任务详情(驾驶舱时间线数据源,spec #8):任务行 + 切片结果(图状态)+ 全部批次。"""
        row = await get_task(thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"任务 {thread_id} 不存在")
        results = None
        if app.state.graph is not None:
            try:
                snapshot = await app.state.graph.aget_state(_config(thread_id))
                if snapshot.values:
                    results = jsonable_encoder(snapshot.values.get("results"))
            except Exception:  # 图状态不可用(线程未跑/checkpointer 缺失):详情仍可用,结果留空
                results = None
        batches = []
        if app.state.batch_store is not None:
            batches = [_serialize_batch(batch) for batch in await app.state.batch_store.list_by_thread(thread_id)]
        return {
            "threadId": row.thread_id,
            "sessionId": row.session_id,
            "type": row.type,
            "status": row.status.value,
            "request": (row.input or {}).get("request"),
            "plan": row.slice_plan,
            "results": results,
            "result": row.result,
            "batches": batches,
            "createdAt": row.created_at.isoformat() if row.created_at else None,
        }

    @app.get("/api/products")
    async def list_products() -> dict:
        """商品列表(只读,spec #9):模拟流量发现商品 + 运营总览数据源。"""
        async with SessionFactory() as session:
            rows = (await session.execute(select(Product).order_by(Product.created_at.desc()))).scalars().all()
        return {"products": [_product_payload(product) for product in rows]}

    @app.get("/api/conversations")
    async def list_user_conversations(request: Request) -> dict:
        """会话列表(spec #9 A2):当前用户的会话,updated_at 倒序。"""
        user_id = _current_user_id(request)
        if user_id is None:
            return {"conversations": []}
        return {"conversations": await list_conversations(user_id)}

    @app.delete("/api/conversations/{session_id}")
    async def remove_conversation(session_id: str, request: Request) -> dict:
        """删除会话(spec #9 A2):有挂起审批批次 → 409(先决定再删);不存在 → 404。"""
        user_id = _current_user_id(request)
        if user_id is None:
            raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
        if await session_has_pending_batches(user_id, session_id):
            raise HTTPException(status_code=409, detail="该会话仍有挂起审批,请先处理后再删除")
        if not await delete_conversation(user_id, session_id):
            raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
        return {"deleted": True}

    @app.get("/api/actions")
    async def list_actions() -> dict:
        """动作元数据(spec #8 遗留收敛:前端标签经 API 获取,不再四处硬编码)。"""
        return {"actions": REGISTRY.metadata()}

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
                await app.state.audit.record(
                    thread_id=thread_id,
                    agent_id="approval",
                    type_="approval_decision",
                    status=decided["decision"],
                    input={"batch_id": batch_id, "comment": decided["comment"]},
                )
        session = await task_session(thread_id)
        await update_task_row(thread_id=thread_id, status="in_progress")
        return await _resume(
            app.state.graph,
            resume_map,
            thread_id,
            app.state.emitter,
            app.state.tracer,
            memory=app.state.memory,
            session_id=session[0] if session else None,
            user_id=session[1] if session else None,
        )

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
                await app.state.audit.record(
                    thread_id=thread_id,
                    agent_id="approval",
                    type_="approval_decision",
                    status=decision,
                    input={"batch_id": batch_id, "comment": body.text},
                )
        session = await task_session(thread_id)
        await update_task_row(thread_id=thread_id, status="in_progress")
        return await _resume(
            app.state.graph,
            resume_map,
            thread_id,
            app.state.emitter,
            app.state.tracer,
            memory=app.state.memory,
            session_id=session[0] if session else None,
            user_id=session[1] if session else None,
        )

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
        await emit_notifications(app.state.emitter, outcome.effects)  # 提交后通知(spec #9)
        return {"status": "executed", "result": {"applied": True}}

    return app
