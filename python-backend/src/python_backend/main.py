"""重构骨架入口:仅 /health 与基础装配;REST/WS 路由、监督图与切片机制在后续增量接入。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from python_backend.db.session import engine
from python_backend.settings import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    logger.info("启动:environment=%s shadow_mode=%s", settings.environment, settings.shadow_mode)
    yield
    await engine.dispose()


app = FastAPI(title="Multi-Agent E-commerce System(重构目标态)", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    db_ok = await _ping_db()
    settings = get_settings()
    return {
        "status": "ok" if db_ok else "degraded",
        "environment": settings.environment,
        "services": {"db": db_ok},
    }


async def _ping_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # 健康检查需吞掉一切连接错误,以 degraded 呈现
        logger.warning("数据库健康检查失败", exc_info=True)
        return False
