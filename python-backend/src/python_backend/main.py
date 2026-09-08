"""重构目标态入口(spec #7 生产装配):

lifespan 内装配 AsyncPostgresSaver(durable interrupt)+ PG 审批批次存储 + 真实业务子图
(三个 Agent 挂接 execute_slice)+ socket.io WS 通道;REST 端点 + /health。
uvicorn 以 wrap_with_socketio 的 ASGI 应用为入口(WS 与 REST 同端口)。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

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
from python_backend.core.graph import default_supervisor, supervisor_serde
from python_backend.db.approval_store import PostgresApprovalBatchStore
from python_backend.db.session import engine
from python_backend.infrastructure.llm import LlmService
from python_backend.infrastructure.tracing import LangfuseTaskTracer
from python_backend.settings import get_settings
from python_backend.vector_repo.milvus_repo import MilvusVectorRepository

logger = logging.getLogger(__name__)

# Windows:psycopg async 不能跑在 ProactorEventLoop 上(uvicorn 默认 loop),
# 必须在 uvicorn 创建事件循环之前切换为 SelectorEventLoop(与测试同策略)。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


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


def build_app() -> socketio.ASGIApp:
    settings = get_settings()
    socketio_server = build_socketio(settings.cors_origin_list)
    emitter = SocketEmitter(socketio_server)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # AsyncPostgresSaver 必须在事件循环内构造(内部绑定 running loop)
        # autocommit=True:setup() 建索引用 CREATE INDEX CONCURRENTLY(不能在事务块内)
        conn = await psycopg.AsyncConnection.connect(conninfo=settings.postgres_dsn, connect_timeout=5, autocommit=True)
        # ty 对 langgraph aio stubs 的 Conn 泛型报 invalid-argument-type(运行时合法,官方文档模式)
        saver = AsyncPostgresSaver(conn, serde=supervisor_serde())  # ty: ignore
        await saver.setup()  # checkpoint 表自建(不入 Alembic)

        batch_store = PostgresApprovalBatchStore()
        graph = default_supervisor(
            checkpointer=saver,
            batch_store=batch_store,
            shadow_mode=settings.shadow_mode,
            agents=build_agents(),
            emitter=emitter,
            tracer=LangfuseTaskTracer(),
        )
        app.state.graph = graph
        app.state.batch_store = batch_store
        app.state.emitter = emitter
        logger.info("启动:environment=%s shadow_mode=%s", settings.environment, settings.shadow_mode)
        yield
        await conn.close()
        await engine.dispose()

    app = create_app(emitter=emitter)
    app.router.lifespan_context = lifespan

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
