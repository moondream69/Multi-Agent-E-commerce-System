"""REST API(spec #6 D4 + spec #7):切片式人工环节端点。

- POST /api/tasks:发起任务(生成 thread_id,图跑至中断/完成)
- GET  /api/approvals:全量未决批次(pending + shadow;影子段仅演练剖面,issue #37)
- GET  /api/threads/{thread_id}/approvals:该 thread 挂起批次列表
- POST /api/threads/{thread_id}/resume:结构化决定(按钮入口,多批一次提交)
- POST /api/threads/{thread_id}/message:自然消息入口(关键词判定决定意图)
- POST /api/threads/{thread_id}/shadow-batches/{batch_id}/execute:影子批次补执行(A15)

WS 实时通道与审批中心 UI 见 api/ws(spec #7)。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
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
    import_customers,
    import_orders,
    import_products,
    parse_customers_csv,
    parse_orders_csv,
    parse_products_csv,
)
from python_backend.core.memory import PostgresSessionMemory, SessionMemory
from python_backend.core.notifications import emit_notifications
from python_backend.db.audit_store import AuditWriter, NullAuditWriter
from python_backend.db.conversation_store import ConversationStore, PostgresConversationStore
from python_backend.db.customer_store import CustomerStore, PostgresCustomerStore
from python_backend.db.models import OrderStatus, User
from python_backend.db.notification_store import NotificationStore, PostgresNotificationStore
from python_backend.db.order_store import (
    DEFAULT_ORDER_LIMIT,
    MAX_ORDER_LIMIT,
    OrderStore,
    PostgresOrderStore,
)
from python_backend.db.product_store import PostgresProductStore, ProductStore
from python_backend.db.report_store import PostgresReportStore, ReportStore
from python_backend.db.session import SessionFactory
from python_backend.db.task_store import PostgresTaskStore, TaskStore
from python_backend.db.ticket_store import PostgresTicketStore, TicketStore
from python_backend.infrastructure.fx import FxQuoteProvider, FxUnavailableError
from python_backend.infrastructure.tracing import NullTaskTracer, TaskTracer

logger = logging.getLogger(__name__)

FX_CARD_CURRENCY = "USD"  # 汇率卡片主体币种(订单默认计价币种;基准仍为 CNY —— ADR-0006)
FX_TREND_WINDOW_DAYS = 7  # 与经营快照「近 7 日成交额」同窗(spec #34)
BASE_CURRENCY = "CNY"


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


class TicketCloseRequest(BaseModel):
    """工单结单(spec #11 A11):唯一合法值 closed(非法值由 pydantic 422 拦截)。"""

    status: Literal["closed"]


class ConversationRenameRequest(BaseModel):
    """会话重命名(spec #11 A2 扩展):trim 后非空且 ≤50 字(端点内校验 422)。"""

    title: str


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


async def _converge_failed_task(task_store: TaskStore, thread_id: str, emitter: EventEmitter, error: Exception) -> None:
    """未预期异常兜底(issue #10):任务行收敛 failed + 广播 task.failed,不留悬挂 in_progress。

    调用方随后原样上抛——编程错误保留 500 观测,不许静默吞掉。
    兜底自身尽力而为:落库/广播再失败只记日志,不遮蔽原始异常。
    """
    reason = f"{type(error).__name__}: {error}"
    try:
        await task_store.update_task_row(
            thread_id=thread_id, status="failed", result={"summary": None, "error": reason}
        )
        await emitter.emit("task.failed", {"threadId": thread_id, "status": "failed", "error": reason})
    except Exception:
        logger.exception("任务 %s 失败兜底未完成(原始异常仍上抛)", thread_id)


async def _update_row_or_converge(task_store: TaskStore, thread_id: str, emitter: EventEmitter, **fields) -> None:
    """任务行写入(关键簿记,spec #11 分类收敛):失败 → 行收敛 failed + 原样上抛(禁止悬挂/自相矛盾)。"""
    try:
        await task_store.update_task_row(thread_id=thread_id, **fields)
    except Exception as error:
        await _converge_failed_task(task_store, thread_id, emitter, error)
        raise


async def _ancillary(work: Awaitable[None], description: str) -> None:
    """辅助簿记(记忆落库/事件广播/计划刷新,spec #11 分类收敛):失败仅记日志,不改任务行、不影响响应。

    与关键簿记的区别:这些丢失只降级观测/上下文,不改变任务已产出的事实;
    失败仍以 logger.exception 上报(可观测,非静默)。
    """
    try:
        await work
    except Exception:
        logger.exception("辅助簿记失败(%s):任务行与响应如实保留", description)


async def _resume(
    graph: CompiledStateGraph,
    resume_map: dict[str, dict],
    thread_id: str,
    emitter: EventEmitter,
    tracer: TaskTracer,
    task_store: TaskStore,
    memory: SessionMemory | None = None,
    session_id: str | None = None,
    user_id: int | None = None,
) -> dict:
    """组装好的 resume_map 驱动图恢复,统一响应形状;完成后广播任务终态事件。

    失败分类(spec #11 §3.3):图执行失败 → 收敛 failed + 上抛;任务行写入失败 → 收敛 failed + 上抛;
    辅助簿记(计划刷新/广播/记忆)失败仅记日志——不改行、响应如实反映任务成果。
    """
    try:
        with tracer.trace(thread_id):
            result = await graph.ainvoke(Command(resume=resume_map), _config(thread_id))
    except Exception as error:
        await _converge_failed_task(task_store, thread_id, emitter, error)
        raise
    if "__interrupt__" in result:
        # 下一层切片又挂起:如实广播(多层切片任务不只一次中断);计划随行刷新(spec #34:
        # 挂起期行内必须有计划,审批中心与切片时间线都读它)
        await _update_row_or_converge(
            task_store,
            thread_id,
            emitter,
            status="interrupted",
            slice_plan=_plan_payload(result.get("plan")),
        )
        await _ancillary(
            emitter.emit("task.interrupted", {"threadId": thread_id, "status": "interrupted"}),
            "task.interrupted 广播",
        )
        return {"status": "interrupted"}
    status = "failed" if result.get("error") else "completed"
    # 图状态为最新规划(含重规划):刷新任务行切片计划,驾驶舱时间线展示重规划后的段
    refreshed_plan = None
    try:
        snapshot = await graph.aget_state(_config(thread_id))
        refreshed_plan = _plan_payload(snapshot.values.get("plan")) if snapshot.values else None
    except Exception:
        logger.exception("任务 %s 切片计划刷新失败(辅助簿记,不改任务行)", thread_id)
    await _update_row_or_converge(
        task_store,
        thread_id,
        emitter,
        status=status,
        slice_plan=refreshed_plan,
        result={"summary": result.get("summary"), "error": result.get("error")},
    )
    await _ancillary(
        emitter.emit(f"task.{status}", {"threadId": thread_id, "status": status, "error": result.get("error")}),
        f"task.{status} 广播",
    )
    if memory is not None and session_id and (result.get("summary") or result.get("error")):
        await _ancillary(
            memory.record(
                session_id,
                user_id,
                role="assistant",
                content=str(result.get("summary") or result.get("error")),
                task_id=thread_id,
            ),
            "助手消息落库",
        )
    return {"status": status, "error": result.get("error"), "summary": result.get("summary")}


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


async def _thread_plans(task_store: TaskStore, thread_ids: list[str]) -> dict[str, dict]:
    """线程级任务上下文旁挂(spec #34;ADR-0005「审批单携带任务上下文+后续计划预览」)。

    形状与任务详情的 ``plan`` 同源(切片计划原样透传,前端复用 SlicePlanSlice 类型),
    外加原始需求 ``request``。计划是**线程**属性,故挂信封而非逐批次重复。
    无任务行/无计划(未认证路径未落行)→ 该线程不入表,由前端按缺省不渲染计划区。
    逐线程取行(N+1,判断级):审批中心线程量级(个位数)下可接受,换取缝的单一真源。
    """
    plans: dict[str, dict] = {}
    for thread_id in dict.fromkeys(thread_ids):  # 去重且保序(同线程多批只查一次)
        row = await task_store.get_task(thread_id)
        if row is None or not row.slice_plan:
            continue
        plans[thread_id] = {"request": (row.input or {}).get("request"), "plan": row.slice_plan}
    return plans


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
    fx_service: FxProvider | FxQuoteProvider | None = None,
    auth_required: bool = True,
    shadow_mode: bool = False,
    memory: SessionMemory | None = None,
    drafting: DraftingService | None = None,
    audit: AuditWriter | None = None,
    task_store: TaskStore | None = None,
    notification_store: NotificationStore | None = None,
    conversation_store: ConversationStore | None = None,
    customer_store: CustomerStore | None = None,
    ticket_store: TicketStore | None = None,
    report_store: ReportStore | None = None,
    product_store: ProductStore | None = None,
    order_store: OrderStore | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    """构建 API 应用:graph/batch_store/apply_fn/emitter/tracer/fx_service/memory/drafting/audit/task_store/
    notification_store/conversation_store/customer_store/ticket_store/report_store/product_store/order_store
    可注入(测试)或由 lifespan 装配(生产)。

    apply_fn 默认真实 apply_batch_actions;emitter/tracer/audit 默认 no-op(spec #7/#8 接缝);
    fx_service 默认由下单端点惰性解析(spec #8 接缝,测试注入假实现;两个子集协议的并集:
    下单/apply 走 FxProvider.get_rate_cny、汇率卡片走 FxQuoteProvider.get_quote_cny,
    生产实现 FxService 二者皆备);
    auth_required 默认开(spec #8 A1 全门禁),非认证行为的测试显式关闭;
    shadow_mode 默认关(prod-safe,与 graph 同名参数默认一致):开=演练剖面,审批中心显示影子段
    且补执行可用;生产由 main 传 settings.shadow_mode(issue #37,验收 B15);
    memory 默认 PG 会话记忆(spec #8 B16 接缝,测试注入内存实现);
    drafting 默认真实起草服务(spec #8 B11 接缝,测试注入假实现);
    task_store 默认 PG 任务行存储(issue #13 接缝,测试注入内存实现——端点流程离线可跑);
    notification_store 默认 PG 通知存储(增量 8-T1 接缝,测试注入内存实现——同上);
    conversation_store / customer_store / ticket_store / report_store 默认 PG 实现(issue #21 接缝
    ——会话/买家/工单/报表四端点族不再对离线快速套件全盲);后两者默认互相接线:
    工单列表的买家名经 customer_store 解析(join 降级为读端点拼装);
    conversation_store 例外:其挂起审批判定依赖批次存储,而 batch_store 生产由 lifespan 构建
    (create_app 时尚不存在)——此处仅在 batch_store 已给时装配 PG 实现,生产装配由
    main.lifespan 补接线(test_app_wiring 守卫该接线)。
    product_store / order_store 默认 PG 实现(spec #34 数据台接缝:商品只读列表 + 订单列表/汇率走势);
    static_dir 默认 None(不托管):生产形态由 main 指向镜像内前端构建产物,同源托管单端口。
    """
    app = FastAPI(title="Multi-Agent E-commerce System(切片式人工环节)", version="0.1.0")
    app.state.graph = graph
    app.state.batch_store = batch_store
    app.state.apply_fn = apply_fn or apply_batch_actions
    app.state.emitter = emitter or NullEmitter()
    app.state.tracer = tracer or NullTaskTracer()
    app.state.auth_enabled = auth_required
    app.state.shadow_mode = shadow_mode
    app.state.fx_service = fx_service  # None 时由下单端点惰性解析(default_fx 需运行中事件循环)
    app.state.memory = memory or PostgresSessionMemory()
    app.state.drafting = drafting or DraftingService()
    app.state.audit = audit or NullAuditWriter()
    app.state.task_store = task_store or PostgresTaskStore()
    app.state.notification_store = notification_store or PostgresNotificationStore()
    # 批次存储可为 None(离线用例不挂审批流);未注入会话存储时其 PG 实现需要批次真源才可用
    app.state.conversation_store = conversation_store or (
        PostgresConversationStore(batch_store) if batch_store is not None else None
    )
    app.state.customer_store = customer_store or PostgresCustomerStore()
    app.state.ticket_store = ticket_store or PostgresTicketStore(app.state.customer_store)
    app.state.report_store = report_store or PostgresReportStore()
    app.state.product_store = product_store or PostgresProductStore()
    app.state.order_store = order_store or PostgresOrderStore()

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
        await _ancillary(
            app.state.memory.record(session_id, user_id, role="user", content=body.request, task_id=thread_id),
            "用户消息落库",
        )
        await app.state.task_store.create_task_row(
            thread_id=thread_id, user_id=user_id, session_id=session_id, type_="chat", request=body.request
        )
        await _ancillary(
            app.state.emitter.emit("task.created", {"threadId": thread_id, "status": "created"}), "task.created 广播"
        )
        try:
            with app.state.tracer.trace(thread_id):
                result = await app.state.graph.ainvoke(
                    SupervisorState(request=body.request, thread_id=thread_id, context=context), _config(thread_id)
                )
        except Exception as error:
            # 图执行失败(spec #11 分类 ①):行收敛 failed + task.failed 后原样上抛 500
            await _converge_failed_task(app.state.task_store, thread_id, app.state.emitter, error)
            raise
        if "__interrupt__" in result:
            # 挂起期即落切片计划(spec #34):审批中心「后续计划预览」与驾驶舱切片时间线读的都是
            # 任务行的 slice_plan,终态才写会让挂起中的任务显示空计划
            await _update_row_or_converge(
                app.state.task_store,
                thread_id,
                app.state.emitter,
                status="interrupted",
                slice_plan=_plan_payload(result.get("plan")),
            )
            await _ancillary(
                app.state.emitter.emit("task.interrupted", {"threadId": thread_id, "status": "interrupted"}),
                "task.interrupted 广播",
            )
            return {"threadId": thread_id, "status": "interrupted"}
        status = "failed" if result.get("error") else "completed"
        # 任务行写入失败(spec #11 分类 ②)→ 收敛 failed + 500;其后广播/记忆失败(分类 ③)不翻行、响应如实
        await _update_row_or_converge(
            app.state.task_store,
            thread_id,
            app.state.emitter,
            status=status,
            slice_plan=_plan_payload(result.get("plan")),
            result={"summary": result.get("summary"), "error": result.get("error")},
        )
        await _ancillary(
            app.state.emitter.emit(
                f"task.{status}", {"threadId": thread_id, "status": status, "error": result.get("error")}
            ),
            f"task.{status} 广播",
        )
        if result.get("summary") or result.get("error"):
            await _ancillary(
                app.state.memory.record(
                    session_id,
                    user_id,
                    role="assistant",
                    content=str(result.get("summary") or result.get("error")),
                    task_id=thread_id,
                ),
                "助手消息落库",
            )
        return {
            "threadId": thread_id,
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
        # 提交后组装通知(spec #9 A8/A9;增量 8-T1 起同时落库):订单创建 + 汇率缺失 + 库存跌破阈值
        await emit_notifications(app.state.notification_store, app.state.emitter, result.effects)
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

    @app.post("/api/import/customers")
    async def import_customers_csv(request: Request) -> dict:
        """买家 CSV 导入(spec #11):email 幂等;导入顺序 买家→订单(订单按邮箱解析买家)。"""
        text = await _csv_text(request)
        try:
            rows, parse_errors = parse_customers_csv(text)
        except CsvFormatError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        report = await import_customers(rows)
        report.errors.extend(parse_errors)
        return {"report": report.as_dict()}

    @app.get("/api/tasks")
    async def list_task_rows(session_id: str | None = None) -> dict:
        """任务列表(驾驶舱数据源,spec #8):最新在前,标题取请求前 20 字(A2 截断语义)。

        session_id 给定时只返回该会话的任务(spec #9 A2:切换会话即切换历史视图)。
        """
        rows = await app.state.task_store.list_tasks(session_id=session_id)
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
        row = await app.state.task_store.get_task(thread_id)
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
        """商品列表(只读,spec #9 / #34):模拟流量发现商品 + 数据台盘货数据源(经注入存储)。"""
        return {"products": await app.state.product_store.list_products()}

    @app.get("/api/orders")
    async def list_order_rows(status: str | None = None, limit: int = DEFAULT_ORDER_LIMIT, offset: int = 0) -> dict:
        """订单列表(只读,spec #34):数据台对账数据源——状态筛选 + 分页 + 同筛选总数。

        **纯只读**(ADR-0006 边界):对外状态变更仍走对话 + 审批护栏,本端点无任何写入口。
        """
        if status is not None and status not in {item.value for item in OrderStatus}:
            raise HTTPException(status_code=422, detail=f"订单状态非法:{status}")
        if not 1 <= limit <= MAX_ORDER_LIMIT:
            raise HTTPException(status_code=422, detail=f"limit 须在 1~{MAX_ORDER_LIMIT} 之间")
        if offset < 0:
            raise HTTPException(status_code=422, detail="offset 不能为负")
        orders, total = await app.state.order_store.list_orders(status=status, limit=limit, offset=offset)
        return {"orders": orders, "total": total}

    @app.get("/api/fx")
    async def fx_card() -> dict:
        """汇率卡片(驾驶舱,ADR-0006 / spec #34):当期汇率(基准 CNY)+ 缓存时刻 + 近 7 日走势。

        走势 = 订单 fx_rate 快照按日聚合(当日最后一笔,真实成交口径):上游 er-api 免费端点
        无时序能力(实测 /v6/history 404),这是唯一不新增外部依赖的时序源。
        当期汇率不可用(API 失效且缓存为空)→ rate 置空由前端显「待核」,不让整卡失败。
        """
        service: FxQuoteProvider = app.state.fx_service or default_fx()
        rate: str | None = None
        cached_at: str | None = None
        source: str | None = None
        try:
            quote = await service.get_quote_cny(FX_CARD_CURRENCY)
        except FxUnavailableError as error:
            logger.warning("汇率卡片:当期汇率不可用(%s)", error)
        else:
            rate = str(quote.rate)
            cached_at = quote.cached_at.isoformat() if quote.cached_at else None
            source = quote.source
        points = await app.state.order_store.daily_fx_snapshots(days=FX_TREND_WINDOW_DAYS, currency=FX_CARD_CURRENCY)
        return {
            "base": BASE_CURRENCY,
            "currency": FX_CARD_CURRENCY,
            "rate": rate,
            "cachedAt": cached_at,
            "source": source,
            "trend": {"windowDays": FX_TREND_WINDOW_DAYS, "points": points},
        }

    @app.get("/api/customers")
    async def list_customer_rows() -> dict:
        """买家列表(只读,spec #11):模拟流量买家池 + 运营查询入口。"""
        return {"customers": await app.state.customer_store.list_customers()}

    @app.get("/api/tickets")
    async def list_ticket_rows() -> dict:
        """工单列表(只读,spec #11 A11):客服升级实体化落表后的界面可见面。"""
        return {"tickets": await app.state.ticket_store.list_tickets()}

    @app.patch("/api/tickets/{ticket_id}")
    async def close_ticket_row(ticket_id: int, body: TicketCloseRequest) -> dict:
        """工单结单(spec #11 A11):open→closed 记 resolved_at;平权(任何登录者)。"""
        ticket = await app.state.ticket_store.close_ticket(ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail=f"工单 {ticket_id} 不存在")
        return {"ticket": ticket}

    @app.get("/api/reports/summary")
    async def report_summary() -> dict:
        """经营快照(spec #11):订单分布 / 近 7 日成交额(CNY 快照口径)/ 低库存 / 未结工单,零 LLM。"""
        return await app.state.report_store.build_summary()

    @app.get("/api/notifications")
    async def list_notifications(request: Request) -> dict:
        """通知真源(增量 8-T2):当前用户通知历史(每组最近 50 条,最新在前)+ 全量未读计数。

        未认证上下文(TaskStore 同语义)返回空态,不触存储;unread 与每组截断解耦。
        """
        user_id = _current_user_id(request)
        if user_id is None:
            return {"notifications": [], "unread": 0}
        notifications = await app.state.notification_store.list_for_user(user_id)
        unread = await app.state.notification_store.unread_count(user_id)
        return {"notifications": notifications, "unread": unread}

    @app.post("/api/notifications/read")
    async def mark_notifications_read(request: Request) -> dict:
        """标记已读(增量 8-T2/T3):幂等清零当前用户未读;未认证跳过落库(同 TaskStore 语义)。

        提交后广播 notification.read(空载荷 poke):各端凭自身 token 重拉——事件不
        携带计数/用户标识(WS 为全量广播无房间无鉴权,携带数值会误清他人客户端)。
        广播为辅助投递(spec #11 分类 ③):失败仅日志,不影响端点成功响应。
        """
        user_id = _current_user_id(request)
        if user_id is not None:
            await app.state.notification_store.mark_read(user_id)
            await _ancillary(app.state.emitter.emit("notification.read", {}), "notification.read 广播")
        return {"unread": 0}

    @app.get("/api/conversations")
    async def list_user_conversations(request: Request) -> dict:
        """会话列表(spec #9 A2):当前用户的会话,updated_at 倒序。"""
        user_id = _current_user_id(request)
        if user_id is None:
            return {"conversations": []}
        return {"conversations": await app.state.conversation_store.list_conversations(user_id)}

    @app.get("/api/conversations/{session_id}/messages")
    async def get_conversation_messages(session_id: str, request: Request) -> dict:
        """会话消息流(spec #20 A2 延伸):该会话全部对话(原序=时间序)+ 会话元数据。

        归属校验按 (user_id, session_id):不存在/非本人 404(与 PATCH/DELETE 同语义);
        未认证 404 且不触存储(读端点缝纪律)。轨迹全量返回、不截断不加参(演示数据量级)。
        """
        user_id = _current_user_id(request)
        if user_id is None:
            raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
        stream = await app.state.conversation_store.get_messages(user_id, session_id)
        if stream is None:
            raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
        return stream

    @app.delete("/api/conversations/{session_id}")
    async def remove_conversation(session_id: str, request: Request) -> dict:
        """删除会话(spec #9 A2):有挂起审批批次 → 409(先决定再删);不存在 → 404。"""
        user_id = _current_user_id(request)
        if user_id is None:
            raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
        if await app.state.conversation_store.has_pending_batches(user_id, session_id):
            raise HTTPException(status_code=409, detail="该会话仍有挂起审批,请先处理后再删除")
        if not await app.state.conversation_store.delete_conversation(user_id, session_id):
            raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
        return {"deleted": True}

    @app.patch("/api/conversations/{session_id}")
    async def rename_user_conversation(session_id: str, body: ConversationRenameRequest, request: Request) -> dict:
        """会话重命名(spec #11 A2 扩展):trim 非空且 ≤50 字(422);不存在/非本人 404。"""
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="标题不能为空")
        if len(title) > 50:
            raise HTTPException(status_code=422, detail="标题最长 50 字")
        user_id = _current_user_id(request)
        if user_id is None:
            raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
        conversation = await app.state.conversation_store.rename_conversation(user_id, session_id, title)
        if conversation is None:
            raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
        return {"conversation": conversation}

    @app.get("/api/actions")
    async def list_actions() -> dict:
        """动作元数据(spec #8 遗留收敛:前端标签经 API 获取,不再四处硬编码)。"""
        return {"actions": REGISTRY.metadata()}

    @app.get("/api/approvals")
    async def list_all_open() -> dict:
        """全量未决批次(pending + shadow):审批中心数据源(spec #7 用户故事 5/6)。

        spec #34:信封加线程级 `plans` 旁挂(原始需求 + 切片计划),补 ADR-0005 的
        「审批单携带任务上下文 + 后续计划预览」;批次载荷形状不变。
        issue #37(验收 B15):影子段仅演练剖面显示——生产剖面在此过滤,前端零剖面感知。
        """
        rows = await app.state.batch_store.list_open()
        if not app.state.shadow_mode:
            rows = [row for row in rows if row.mode != "shadow"]
        batches = [_serialize_batch(row) for row in rows]
        plans = await _thread_plans(app.state.task_store, [batch["threadId"] for batch in batches])
        return {"approvals": batches, "plans": plans}

    @app.get("/api/threads/{thread_id}/approvals")
    async def list_approvals(thread_id: str) -> dict:
        """该 thread 未决批次列表(信封同构:approvals + plans 旁挂,spec #34)。"""
        batches = [_serialize_batch(batch) for batch in await app.state.batch_store.list_pending(thread_id)]
        plans = await _thread_plans(app.state.task_store, [thread_id])
        return {"approvals": batches, "plans": plans}

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
        session = await app.state.task_store.task_session(thread_id)
        await app.state.task_store.update_task_row(thread_id=thread_id, status="in_progress")
        return await _resume(
            app.state.graph,
            resume_map,
            thread_id,
            app.state.emitter,
            app.state.tracer,
            app.state.task_store,
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
        session = await app.state.task_store.task_session(thread_id)
        await app.state.task_store.update_task_row(thread_id=thread_id, status="in_progress")
        return await _resume(
            app.state.graph,
            resume_map,
            thread_id,
            app.state.emitter,
            app.state.tracer,
            app.state.task_store,
            memory=app.state.memory,
            session_id=session[0] if session else None,
            user_id=session[1] if session else None,
        )

    @app.post("/api/threads/{thread_id}/shadow-batches/{batch_id}/execute")
    async def execute_shadow_batch(thread_id: str, batch_id: str) -> dict:
        """影子批次补执行(A15):演练剖面高危建议由人工一键补执行。

        issue #37(验收 B15):生产剖面如实 403——影子批次未经人工批准,补执行即绕过审批锁。
        """
        if not app.state.shadow_mode:
            raise HTTPException(status_code=403, detail="影子批次补执行仅演练剖面可用")
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
        await emit_notifications(app.state.notification_store, app.state.emitter, outcome.effects)  # 提交后通知
        return {"status": "executed", "result": {"applied": True}}

    # 生产形态(ADR-0005「部署」节):前端构建产物由本进程同源托管——单端口,WS 免反代,CORS 退出关键路径。
    # 开发/离线(无 dist)不挂载:根路径保持 404,前端走 Vite(5173 代理)。
    # 有意不加 SPA 回退:前端无 URL 驱动导航;且 /health 由 main 在本函数返回后才注册,
    # 此处任何 catch-all 都会按注册序吞掉它。dist 根级新增资源须在此扩挂(前端无 public/ 目录)。
    if static_dir is not None and (static_dir / "index.html").is_file():
        index_file = static_dir / "index.html"

        @app.get("/", include_in_schema=False)
        def root_page() -> FileResponse:
            """入口须每次回源:重建后 hash 资源名变更,启发式缓存旧入口会白屏。"""
            return FileResponse(index_file, headers={"Cache-Control": "no-cache"})

        # 单独守卫:StaticFiles 构造期即校验目录存在,裸挂缺失目录会直接抛 RuntimeError
        assets = static_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

    return app
