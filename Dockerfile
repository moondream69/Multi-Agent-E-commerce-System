# 重构骨架:仅后端(python:3.13-slim + uv)。前端静态托管在前端增量接入后恢复多阶段构建。
FROM python:3.13-slim
WORKDIR /app
ENV UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uv/bin/uv
COPY python-backend/pyproject.toml python-backend/uv.lock ./
COPY python-backend/src ./src
COPY python-backend/alembic ./alembic
COPY python-backend/alembic.ini ./alembic.ini
# --no-dev 不装测试依赖;uv.lock 由本地 uv sync 生成后随仓库
RUN uv sync --no-dev

EXPOSE 3000
# 单进程 asyncio 并发(宪章 Q22:并发靠协程与 thread 隔离,非多 worker);统一入口负责 Windows 事件循环兼容
CMD ["sh", "-c", "uv run alembic upgrade head && uv run python -m python_backend.run"]
