# 多阶段构建:node 构建前端 → python 镜像打包后端 + 静态资源(单端口 3000 服务前后端)
FROM node:22-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
ENV UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uv/bin/uv
COPY python-backend/pyproject.toml python-backend/uv.lock ./
COPY python-backend/src ./src
COPY python-backend/alembic ./alembic
COPY python-backend/alembic.ini ./alembic.ini
RUN uv sync --frozen --no-dev

COPY --from=frontend-build /build/dist ./dist

EXPOSE 3000
# 迁移与 seed 均幂等;单 worker 是架构硬约束(EventBus 进程内 + 审批等待协程)
CMD ["sh", "-c", "uv run alembic upgrade head && uv run python -m python_backend.seed && uv run uvicorn python_backend.main:app --host 0.0.0.0 --port 3000 --workers 1"]
