# Multi-Agent E-commerce System

多 Agent 协作的跨境电商系统(中国出海场景):用户的自然语言请求经 Manager 规划为切片计划,由三个业务 Agent(选品 / 订单 / 客服)在监督图中逐步执行;一切对外可见的高危动作**不立即生效**——登记进审批批次,人工批准后在事务内统一执行。

> **当前处于推翻式重构期**:`main` 冻结旧系统;现行目标态在 **`rebuild` 分支**(架构宪章 = `docs/adr/0005-architecture-rebuild-production-charter.md`)。本 README 描述目标态;开发命令、约定与排坑以 `CLAUDE.md` 为准。

## 它怎么工作

```
用户输入 (REST /api/tasks) → Manager 规划(≤5 切片 + 依赖声明) → 监督图逐层 Send 扇出
                             → 业务子图(免审直行、审批动作收集参数快照) → 按类型打包批次
                             → 切片边界 interrupt → 批准后事务内 apply(批内同进同退) → 汇总
```

三条执行原则:

- **免审直行 / 审批效果后置**:只读与草稿编辑直接执行;上架、改价、删除、订单流转、取消等对外动作只登记参数快照,批准后才在一个事务里统一生效(批内同进同退,快照漂移整批回滚)
- **切片边界可挂起**:运行状态落库(checkpointer),审批决定后从断点恢复,可跨进程重启(durable interrupt)
- **全过程可观测**:WebSocket 实时事件流、通知铃铛、驾驶舱经营快照、审批中心与工单列表

## 技术栈

FastAPI + LangGraph · PostgreSQL 16(业务数据) · Milvus(向量检索) · Redis 7 · DeepSeek v4 Flash(LLM) · 本地 BGE-M3 via Ollama(Embedding,1024 维) · python-socketio · React + Vite · uv

## 快速开始

```bash
cp .env.example .env        # 填入 LLM key 等;Embedding 默认指向本机 Ollama:11434(需 ollama pull bge-m3)
docker compose up -d        # 起 Postgres/Redis/Milvus/app/Langfuse 等(首次先 docker compose build)

cd python-backend
uv run alembic upgrade head             # 数据库迁移(12 业务表;checkpoint 表由 PostgresSaver 自建)
uv run python -m python_backend.run     # 本机起后端 :3000(Windows 必走 run.py;容器方案由 compose 托管)

cd frontend && npm install && npm run dev   # 前端 :5173
```

管理员与演示买家由应用启动时幂等 seed(凭据取 `.env` 的 `AUTH_ADMIN_*`)。

模拟流量(需后端在线;真 HTTP 入口,走完整任务/下单链路):

```bash
cd python-backend && uv run python -m python_backend.simulator --once     # 冒烟一轮(命中下单会等 30-90s)
```

## 测试与检查

```bash
cd python-backend && uv run pytest                       # 全部(集成/e2e 需真实服务在线,离线秒 skip)
cd python-backend && uv run ruff check . && uv run ty check .
cd frontend && npm run lint && npm test && npm run build # vitest 组件测试
```

CI(`.github/workflows/ci.yml`)在 push(main/rebuild)与 PR 上跑上述检查。

## 目录与文档地图

| 路径 | 内容 |
|------|------|
| `python-backend/` | FastAPI + LangGraph 后端(唯一后端;结构见其 README) |
| `frontend/` | React + Vite 前端(驾驶舱 / 客服工作台 / 审批中心 / 工单) |
| `docs/adr/` | 架构决策记录;现行约束 = **ADR-0005** |
| `docs/acceptance-scenarios.md` | 验收基线(A/B 场景清单) |
| `docs/OPERATIONS.md` | 运维手册:启动、备份/恢复、审计 SQL |
| `CONTEXT.md` | 术语表(目标态,以它为准) |
| `CLAUDE.md` | 开发命令与约定(AI 会话入口) |
