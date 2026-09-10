# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 当前状态:推翻式重构期

旧系统冻结在 main(880b62d);**rebuild 分支按 ADR-0005 重写中**(增量 1-7 已落地 + issue #12 修复,下会话增量 8,交接见 `docs/handoffs/`)。README.md 停留在旧系统(自 main 冻结起未更新,含旧类名/旧表描述与必挂的 uvicorn 直启命令),以本文件 + CONTEXT.md 为准。业务决策唯一约束 = @docs/adr/0005-architecture-rebuild-production-charter.md;验收基线 = @docs/acceptance-scenarios.md;术语表(目标态,以它为准)= @CONTEXT.md。旧系统术语/类名只在被取代的决策记录中保留,不得当作现行架构。

## 开发命令

```bash
# 基础设施
docker compose up -d                                          # 启动 9 服务:Postgres/Redis/app 之外,Milvus/etcd/MinIO/ClickHouse/Langfuse×2 属默认栈(模拟流量 --profile sim、Ollama --profile embed 按需;首次需 docker compose build)

# 后端 (python-backend/,Python 版为唯一后端)
cd python-backend
uv run python -m python_backend.run                # 启动(端口 3000;Windows 下经 run.py 切 SelectorEventLoop——uvicorn 直接跑 main 会因 psycopg 不支持 Proactor 而启动失败)
uv run pytest                                      # 全部测试(e2e/integration 需真实服务在线,离线秒 skip)
uv run pytest -m "not e2e and not integration"     # CI 同款快速套件
uv run alembic upgrade head                        # 数据库迁移(12 业务表;checkpoint 表由 PostgresSaver 自建,不在 Alembic 内)
uv run ruff check .                                # Lint (无 --fix,自动修复用 `ruff check . --fix`)
uv run ruff format .                               # 格式化
uv run ty check .                                  # 类型检查 (Alembic 迁移已排除)

# 前端 (另开终端)
cd frontend && npm run dev                           # Vite (5173)
cd frontend && npm run lint / lint:fix               # ESLint 检查/自动修复 (前端,无 --fix 不改写)
cd frontend && npm run format / format:check         # Prettier 格式化/只检查
cd frontend && npm test                              # vitest 组件测试(新增/修改组件;test:watch 监听)
cd frontend && npm run build                         # 前端构建 (tsc + vite)

# 模拟流量 (需后端已启动;容器内为 docker compose --profile sim up -d)
cd python-backend && uv run python -m python_backend.simulator --once             # 冒烟一轮
cd python-backend && uv run python -m python_backend.simulator --loop 300         # 每 300 秒一轮
```

> ⚠️ `npm run lint` 只检查、不自动改写——需要自动修复时用 `npm run lint:fix`。
> lint/format 已移入前端:所有 npm 命令须在 `frontend/` 下执行(仓库根已无 package.json)。
> Python 侧规范工具为 ruff(lint+format)与 ty(type check),配置在 `python-backend/pyproject.toml`。
> uv 在 PATH(`E:\Python\Scripts\uv.exe`)。PyPI 直连不畅时:`HTTPS_PROXY=http://127.0.0.1:7897 uv sync`。
> ⚠️ 改后端代码后须 `docker compose build app && docker compose up -d app`——app 镜像 COPY 源码、无挂载,不重建即跑旧码。
> ⚠️ `alembic check` 只看有无 `modify_type` 判漂移(`checkpoint_*` 与 `uq_orders_reference_partial` 恒报 remove 类噪声),勿整体非零即慌。
> ⚠️ ty 有平台差异:Windows 专属分支(`if sys.platform == "win32":`)里的 `# ty: ignore` 在 Linux 目标下会被判"未使用"而致 CI 红。推送前用 `uv run ty check --python-platform linux .` 复现 CI。
> CI(`.github/workflows/ci.yml`)在 push(main/rebuild)与 PR 上跑:后端 ruff/ty/快速 pytest,前端 lint/vitest/build。

## 技术栈

FastAPI + LangGraph · PostgreSQL 16(向量在 Milvus,不入 PG;访问经 VectorRepository)· Redis 7 · DeepSeek v4 Flash (LLM) · 本地 BGE-M3 via Ollama (Embedding, 1024维) · python-socketio (WS 广播) · React + Vite + socket.io-client · uv

## 核心架构

```
用户输入 (REST /api/tasks) → Manager 规划(≤5 切片 + 依赖声明) → 监督图逐层 Send 扇出
                             → 业务子图(免审直行、审批动作收集参数快照) → 按类型打包批次
                             → 切片边界 interrupt → 批准后事务内 apply(批内同进同退) → 汇总
```

### 关键模块与职责 (python-backend/src/python_backend/)

| 模块 | 文件 | 职责 |
|-----|------|------|
| `ManagerPlanner` | `core/planning.py` | LLM 规划切片计划(≤5 步)+ 校验重试 + fallback 关键词路由;`AGENTS` 元组为业务域清单 |
| 监督图 | `core/graph.py` | manager → prepare → 逐层 Send 扇出 → execute_slice(子图挂接/批次打包/interrupt/apply)→ 拒后回流重规划(REPLAN_LIMIT=2) |
| 业务子图 | `agents/{product_research,order_management,customer_service}/` | ReAct 循环(10 步上限,B17);客服为结构化 verify→draft 两节点(图级查证优先,B12) |
| `ToolExecutor` | `agents/executor.py` | auto 直行 / approval 收集参数快照 / `apply_batch_actions`(事务+行锁+快照比对,漂移整批回滚,B18) |
| 审批批次 | `core/approvals.py` + `db/approval_store.py` | 三层风险分类、`ApprovalBatchStore` 协议(create/decide 幂等,重放安全)、PG 实现 |
| `VectorRepository` | `vector_repo/base.py` | 向量访问抽象(Milvus 实现,pgvector 可切换) |
| 事件与观测 | `core/events.py` / `infrastructure/tracing.py` | `EventEmitter`(WS 事件)/ `TaskTracer`(Langfuse 层级,B14) |
| 通知组装 | `core/notifications.py` | 效果描述→通知载荷(状态映射表 7 文案 + 五档库存文案,零 LLM);`notification.created` 由 apply/REST **提交后** emit |
| `LlmService` | `infrastructure/llm.py` | `complete()` + `complete_with_tools()`(function calling);失败统一包装 `LlmFailure`(fallback 只承接它) |

### Agent 模式

三个业务 Agent 是 LangGraph 子图,只声明身份与工具清单,由 LLM 决定调用顺序;**审批动作调用后不立即生效**——登记待人工批准,批准后事务内统一执行(效果后置):

```
选品  build_product_agent: trend_query / competitor_analysis / scoring / generate_report / draft_create(报告→自动草稿,A4)
订单  build_order_agent: 只读与 draft 编辑免审;上架/下架/改价/删除/订单流转/取消进审批(效果后置)
客服  build_customer_agent: verify(faq_search / order_lookup)→ draft 两节点,未查证不可达草稿(B12)
```

### 如何新增 Agent

1. `agents/<name>/tools.py`:定义 `ToolDefinition` 清单(OpenAI function 形状;工具名与动作标识经 `action_of` 显式映射)
2. `agents/<name>/agent.py`:`build_<name>_agent(executor, llm)` 返回 (编译子图, ToolRegistry);结构化图直接手绘节点
3. 挂接:`core/planning.py` 的 `AGENTS` 元组 + Manager 提示词领域路由;`main.py` 的 `build_agents()`
4. 新动作一处注册:`agents/executor.py` 中集中注册(REGISTRY.register:风险分类/中文标签/处理函数;`agents/registry.py` 仅定义类);前端标签经 GET /api/actions 渲染,不再硬编码。审批动作的 `apply` 若产生对外可见效果,**返回效果描述**(`core/notifications.py` 的 `EFFECT_*`),由调用方在事务提交后组装通知——不要在事务内 emit

## 环境配置

字段以 `python-backend/src/python_backend/settings.py` 为准(仓库根 `.env` 单一真源,compose 共读);下表列关键项:

| 变量 | 用途 |
|------|------|
| `database_url` | PostgreSQL 连接(SQLAlchemy URL,psycopg 直连经 `postgres_dsn` 属性去前缀) |
| `redis_host` / `redis_port` | Redis 连接 |
| `llm_api_key` / `llm_api_url` / `llm_model` | DeepSeek(OpenAI 兼容协议) |
| `llm_max_concurrency` | LLM 并发闸(默认 2,DeepSeek 账号级限流防护) |
| `embedding_api_url` / `embedding_model` / `embedding_dimension` | Ollama bge-m3(1024 维);无 OpenAI 降级,不可用显式报错 |
| `milvus_uri` | Milvus Standalone 端点 |
| `langfuse_host` / `langfuse_public_key` / `langfuse_secret_key` | 自托管观测,host 留空即禁用(no-op) |
| `environment` | 环境剖面:`dev`=演练(影子模式)/ `prod`=生产(审批锁死);影子模式由它派生,运行时不可切换 |
| `auth_jwt_secret` / `auth_token_ttl_hours` | JWT 签名密钥(生产必改:`openssl rand -hex 32`)/ 有效期 |
| `auth_admin_username` / `auth_admin_password` | 初始管理员凭据(启动时 lifespan 懒 seed;密码留空则跳过) |
| `approval_ttl_hours` | 审批批次存活时长(预留:当前无自动过期清扫,见 docs/OPERATIONS.md) |
| `cors_origins` | 允许的跨域来源列表 |
| `fx_api_url` | 汇率 API(基准 CNY;Redis 缓存 4h,下单快照落库) |

> ⚠️ Embedding 服务不可用时 `EmbeddingService` 显式报错(不静默降级为零向量)。
> 前端类型是 API 契约唯一真源:`frontend/src/types/events.ts`(对应契约测试 `python-backend/tests/test_contract.py`)。

## 数据库约定

- 枚举 status 列一律经 `db/models.py` 的 `_status_column_type()` 声明(`native_enum=False` + `values_callable`,落库 = 小写 value,与迁移/server_default/JSON 契约一致);**新增枚举列照抄,勿靠 `Mapped[X]` 推断**(推断出原生枚举 → 批插渲染 `::<名>status` 报错,issue #12);改口径 = 数据迁移
- dev 库 = `mae`(测试直写,带 tag 行会累积);`multi_agent_ecommerce` 是旧系统冻结库,**勿动**

## Agent skills

### Issue tracker

Issue 跟踪在 GitHub Issues,用 `gh` CLI 读写。见 `docs/agents/issue-tracker.md`。

### Triage labels

五个规范角色直接作为标签名(`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`)。见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局:仓库根一个 `CONTEXT.md`,ADR 在 `docs/adr/`。见 `docs/agents/domain.md`。

### Session handoffs

每会话收尾写 `docs/handoffs/session-handoff-<date>-<topic>.md`(该目录 gitignore,不入库);开头写明"下会话主题"。
