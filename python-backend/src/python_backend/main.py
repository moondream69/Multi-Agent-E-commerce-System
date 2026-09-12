"""重构目标态入口(spec #7 生产装配):

lifespan 内装配 AsyncPostgresSaver(durable interrupt)+ PG 审批批次存储 + 真实业务子图
(三个 Agent 挂接 execute_slice)+ socket.io WS 通道;REST 端点 + /health。
uvicorn 以 wrap_with_socketio 的 ASGI 应用为入口(WS 与 REST 同端口)。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
import socketio
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text

from python_backend.agents.base import AgentRunner, make_agent_runner
from python_backend.agents.customer_service.agent import build_customer_agent
from python_backend.agents.executor import ToolExecutor
from python_backend.agents.order_management.agent import build_order_agent
from python_backend.agents.product_research.agent import build_product_agent
from python_backend.api.app import create_app
from python_backend.api.ws import SocketEmitter, build_socketio, wrap_with_socketio
from python_backend.core.auth import ensure_admin_user
from python_backend.core.drafting import DraftingService
from python_backend.core.graph import default_supervisor, supervisor_serde
from python_backend.db.approval_store import PostgresApprovalBatchStore
from python_backend.db.audit_store import PgAuditWriter
from python_backend.db.conversation_store import PostgresConversationStore
from python_backend.db.session import engine
from python_backend.infrastructure.llm import LlmService
from python_backend.infrastructure.tracing import LangfuseTaskTracer
from python_backend.settings import get_settings
from python_backend.vector_repo.milvus_repo import MilvusVectorRepository

logger = logging.getLogger(__name__)

# Windows 事件循环策略由包入口 __init__.py 统一设置(psycopg 异步需 SelectorEventLoop;
# import 本模块必先执行包 __init__,故无需在此重复;正式入口 run.py 用 loop_factory)。


def build_agents() -> dict[str, AgentRunner]:
    """三个业务 Agent 子图挂接为 AgentRunner(spec #7:替换 stub runner)。"""
    llm = LlmService()
    executor = ToolExecutor(llm=llm, vector=MilvusVectorRepository())
    product_graph, _ = build_product_agent(executor=executor, llm=llm)
    order_graph, _ = build_order_agent(executor=executor, llm=llm)
    customer_graph, _ = build_customer_agent(executor=executor, llm=llm)
    return {
        "product_research": make_agent_runner(product_graph),
        "order_management": make_agent_runner(order_graph),
        "customer_service": make_agent_runner(customer_graph),
    }


def _resolve_static_dir() -> Path | None:
    """前端构建产物目录:多阶段构建 COPY 至镜像 /app/dist(本文件 parents[2] 即 /app)。

    本地 uv 开发指向 python-backend/dist(不存在)→ None,前端仍走 Vite(ADR-0005「部署」节)。
    """
    dist = Path(__file__).resolve().parents[2] / "dist"
    return dist if dist.is_dir() else None


def build_app() -> socketio.ASGIApp:
    settings = get_settings()
    socketio_server = build_socketio(settings.cors_origin_list)
    emitter = SocketEmitter(socketio_server)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await ensure_admin_user()  # A1:初始管理员懒 seed(幂等;未配置凭据则跳过)
        await app.state.customer_store.ensure_demo_buyers()  # spec #11:演示买家懒 seed(仅 dev;幂等)
        # AsyncPostgresSaver 必须在事件循环内构造(内部绑定 running loop)
        # autocommit=True:setup() 建索引用 CREATE INDEX CONCURRENTLY(不能在事务块内)
        conn = await psycopg.AsyncConnection.connect(conninfo=settings.postgres_dsn, connect_timeout=5, autocommit=True)
        # ty 对 langgraph aio stubs 的 Conn 泛型报 invalid-argument-type(运行时合法,官方文档模式)
        saver = AsyncPostgresSaver(conn, serde=supervisor_serde())  # ty: ignore
        await saver.setup()  # checkpoint 表自建(不入 Alembic)

        batch_store = PostgresApprovalBatchStore()
        audit = PgAuditWriter()
        graph = default_supervisor(
            checkpointer=saver,
            batch_store=batch_store,
            shadow_mode=settings.shadow_mode,
            agents=build_agents(),
            emitter=emitter,
            tracer=LangfuseTaskTracer(),
            audit=audit,
        )
        app.state.graph = graph
        app.state.batch_store = batch_store
        # 会话存储的批次依赖须待批真实例存在后才装(create_app 时 batch_store 尚未构建)
        app.state.conversation_store = PostgresConversationStore(batch_store)
        app.state.emitter = emitter
        app.state.audit = audit
        logger.info("启动:environment=%s shadow_mode=%s", settings.environment, settings.shadow_mode)
        yield
        await conn.close()
        await engine.dispose()

    # drafting 接真实向量仓库:查证优先硬约束的生产装配(spec #8 B11)
    static_dir = _resolve_static_dir()
    app = create_app(
        emitter=emitter,
        drafting=DraftingService(vector=MilvusVectorRepository()),
        static_dir=static_dir,
    )
    app.router.lifespan_context = lifespan
    logger.info("前端静态托管:%s", static_dir or "未启用(开发态走 Vite 5173)")

    @app.get("/health")
    async def health() -> dict:
        db_ok = await _ping_db()
        return {
            "status": "ok" if db_ok else "degraded",
            "environment": settings.environment,
            "services": {"db": db_ok},
        }

    return wrap_with_socketio(app, socketio_server)


async def _ping_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # 健康检查需吞掉一切连接错误,以 degraded 呈现
        logger.warning("数据库健康检查失败", exc_info=True)
        return False


app = build_app()
