# 多阶段:node 构建前端静态产物 → python 镜像打包后端 + 静态资源(单端口 3000 同源服务前后端)。
# 构建阶段用 slim(glibc)而非 alpine:CI 的 npm run build 只在 glibc 上验证过,tailwind oxide /
# rolldown 的原生二进制虽在 lock 里有 musl 变体,但未经 CI 覆盖,不值得在生产构建路径上赌。
# 产物定位见 main._resolve_static_dir(CONTEXT:静态托管)。
FROM node:22-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
ENV UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# uv 镜像是单二进制(/uv 即可执行文件);拷到 PATH 内位置,否则 RUN 里的 uv 找不到
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
COPY python-backend/pyproject.toml python-backend/uv.lock ./
COPY python-backend/src ./src
COPY python-backend/alembic ./alembic
COPY python-backend/alembic.ini ./alembic.ini
# --no-dev 不装测试依赖;uv.lock 由本地 uv sync 生成后随仓库
RUN uv sync --no-dev

# 产物置于依赖层之后:前端改动不击穿 uv sync 缓存(/app/dist 即 main 的 parents[2]/dist)
COPY --from=frontend-build /build/dist ./dist

EXPOSE 3000
# 单进程 asyncio 并发(宪章 Q22:并发靠协程与 thread 隔离,非多 worker);统一入口负责 Windows 事件循环兼容
CMD ["sh", "-c", "uv run alembic upgrade head && uv run python -m python_backend.run"]
