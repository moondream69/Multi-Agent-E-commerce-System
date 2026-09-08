"""重构目标态入口(增量 3 生产装配,spec #6 D1/D4):

lifespan 内装配 AsyncPostgresSaver(durable interrupt)+ PG 审批批次存储;
REST 四端点 + /health。WS 实时通道与审批中心 UI 随增量 4/5。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text

from python_backend.api.app import create_app
from python_backend.core.graph import default_supervisor, supervisor_serde
from python_backend.db.approval_store import PostgresApprovalBatchStore
from python_backend.db.session import engine
from python_backend.settings import get_settings

logger = logging.getLogger(__name__)


def build_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # AsyncPostgresSaver 必须在事件循环内构造(内部绑定 running loop)
        # autocommit=True:setup() 建索引用 CREATE INDEX CONCURRENTLY(不能在事务块内)
        conn = await psycopg.AsyncConnection.connect(conninfo=settings.postgres_dsn, connect_timeout=5, autocommit=True)
        # ty 对 langgraph aio stubs 的 Conn 泛型报 invalid-argument-type(运行时合法,官方文档模式)
        saver = AsyncPostgresSaver(conn, serde=supervisor_serde())  # ty: ignore
        await saver.setup()  # checkpoint 表自建(不入 Alembic)

        batch_store = PostgresApprovalBatchStore()
        graph = default_supervisor(checkpointer=saver, batch_store=batch_store, shadow_mode=settings.shadow_mode)
        app.state.graph = graph
        app.state.batch_store = batch_store
        logger.info("启动:environment=%s shadow_mode=%s", settings.environment, settings.shadow_mode)
        yield
        await conn.close()
        await engine.dispose()

    app = create_app()
    app.router.lifespan_context = lifespan

    @app.get("/health")
    async def health() -> dict:
        db_ok = await _ping_db()
        return {
            "status": "ok" if db_ok else "degraded",
            "environment": settings.environment,
            "services": {"db": db_ok},
        }

    return app


async def _ping_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # 健康检查需吞掉一切连接错误,以 degraded 呈现
        logger.warning("数据库健康检查失败", exc_info=True)
        return False


app = build_app()
