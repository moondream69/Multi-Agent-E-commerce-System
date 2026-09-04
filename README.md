# Multi-Agent E-commerce System

多 AI Agent 协作的跨境电商系统，面向中国出海电商场景。三个 Agent（选品分析 / 订单处理 / 智能客服）各自携带专用工具，由 LLM 驱动的 ReAct 推理自主决策工具调用顺序；Agent 间通过**事件总线**松耦合协作——选品报告自动生成商品草稿、订单状态变化触发客服主动通知，形成从「选品」到「售后」的完整业务闭环。

前端提供双重视角：**驾驶舱**（Agent 状态 + 实时事件流）与**买家前台**（商品商店 → 下单 → 订单 → 售后客服）。

## 技术栈

FastAPI + LangGraph · PostgreSQL 16 + pgvector (向量检索) · Redis 7 · DeepSeek v4 Flash (LLM) · 本地 BGE-M3 via Ollama (Embedding, 1024 维) · React + Vite + socket.io-client · uv

## 快速开始

```bash
cp .env.example .env                    # 编辑 .env 填入实际配置 (EMBEDDING_API_URL 用 Ollama:11434)
docker compose up -d                    # 启动 PostgreSQL + Redis
# Embedding 由本机 Ollama (http://localhost:11434, 模型 bge-m3) 提供——先 `ollama pull bge-m3` 确认模型已拉取

cd python-backend
uv run alembic upgrade head             # 数据库迁移 (10 表,含 pgvector 扩展)
uv run python -m python_backend.seed    # 数据播种 (幂等,按自然键跳过已存在记录)
uv run uvicorn python_backend.main:app --port 3000   # 启动后端 (端口 3000,前端契约不变)

# 前端 (另开终端)
cd frontend && npm install && npm run dev  # Vite 开发服务器 (端口 5173)
```

## 核心架构

```
用户输入 (聊天 / REST) → IntentParser → Orchestrator → Agent.handleTask()
                                                           │
                                          BaseAgent (模板方法: 状态机 + 错误兜底)
                                                              │
                                          LangGraph StateGraph (ReAct 循环)
                                                              │
                                          LLM 选工具 → 工具执行 → 观察 → 再推理 → 输出
```

### Agent 间协作(事件总线)

工具在业务动作完成后 `emit` 领域事件，其他 Agent 订阅并做出反应——跨 Agent 数据流因此形成闭环：

```
选品报告 report.generated ──→ 订单 Agent: LLM 提炼商品 → product_crud 创建草稿
订单状态 order.status_changed ──→ 客服 Agent: 生成通知 → chat:notification 推送聊天面板
库存告警 inventory.alert ──→ 客服 Agent: 转发通知
客服回复 reply.generated / 升级 escalation.triggered ──→ 事件流展示
```

演示买家「张伟」(seed 客户,email `zhangwei@example.com`)在商店页直接下单,REST 创建 pending 订单;订单状态流转由订单 Agent 在聊天中指挥完成,状态变化即触发客服主动通知。

### 关键组件

| 组件 | 职责 |
|------|------|
| `BaseAgent` (`core/base_agent.py`) | 模板方法：状态机 + `executeTask()` 调用 LangGraph 图 |
| `ReAct 图` (`core/graph.py`) | LangGraph 手绘 StateGraph：LLM 选工具 → 执行 → 观察 → 循环直到输出最终答案(同步 LLM 调用经 `asyncio.to_thread` 卸载出事件循环) |
| `Workflow` (`core/workflow.py`) | 可选图级约束：阶段必调工具集 / 工具白名单 / 可否直接回答 |
| `Orchestrator` (`core/orchestrator.py`) | Agent 注册 + 任务路由 + 任务审计 (agent_tasks 表) |
| `EventBus` (`core/event_bus.py`) | Agent 间松耦合事件通信，`emit()` / `on()` / `broadcast()` |
| `LlmService` (`infrastructure/llm.py`) | `complete()` (纯文本,Redis 缓存) + `completeWithTools()` (function calling) |
| `EmbeddingService` (`infrastructure/embedding.py`) | `embed()` (→1024 维向量) + `search()` (pgvector 余弦相似度) |
| `ITool` (`domain/tools.py`) | 所有工具的标准化接口：`{ definition; execute(params) }`(可注入 EventBus emit 事件) |

### Agent 清单

三个 Agent 不写死业务逻辑，只声明身份和工具清单，由 LLM 自主决定工具调用顺序：

| Agent | 工具 |
|-------|------|
| **ProductResearchAgent** | `trend_query`, `competitor_analysis`, `scoring`, `generate_report` |
| **OrderManagementAgent** | `product_crud`, `order_workflow`, `check_inventory`, `detect_anomalies` |
| **CustomerServiceAgent** | `translate`, `faq_search`, `sentiment_analysis`, `manage_template`, `order_lookup`, `escalate_ticket` |

客服 Agent 声明两阶段 Workflow:先必调 `sentiment_analysis` + `faq_search`,完成后解锁全部工具可自由回答(图级白名单裁剪,未声明 Agent 行为不变)。

## 事件类型(12 类)

| 事件 | 触发方 | 消费方 |
|------|--------|--------|
| `report.generated` | 选品 `generate_report` | 订单 Agent → 自动创建商品草稿 |
| `product.created` / `product.updated` | 订单 `product_crud` | 前端商店实时刷新 |
| `order.status_changed` | 订单 `order_workflow` / 商店下单 | 客服 Agent → 主动通知;前端订单实时刷新 |
| `reply.generated` | 客服 `manage_template.fill` | 事件流展示 |
| `escalation.triggered` | 客服 `escalate_ticket` | 事件流展示 |
| `inventory.alert` | 订单 `check_inventory` (告警时) | 客服 Agent → 转发通知 |
| `customer.notification` | 客服 Agent | WS 桥接 `chat:notification` → 聊天面板 |
| `task.assigned` / `task.completed` / `task.failed` | Orchestrator | 事件流展示 |
| `agent.status_changed` | BaseAgent 状态机 | 驾驶舱 Agent 状态徽标 |

## REST 端点

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/agents/task` | 创建并路由 Agent 任务 |
| GET | `/api/agents/{id}` | 查询 Agent 信息 |
| GET | `/api/dashboard/agents` | 全部 Agent 列表 |
| GET | `/api/dashboard/status` | Agent 在线统计 |
| GET | `/api/products` (`?category=`) | 商店商品列表(仅 active) |
| GET | `/api/products/{id}` | 商品详情 |
| POST | `/api/orders` | 买家下单(演示买家,金额缺省取商品价) |
| GET | `/api/orders` | 订单列表(含嵌套商品) |

WebSocket:`chat:message` → `chat:response`(task_created / task_result / task_error 三形状);服务端推送 `agent:event`(全量事件)与 `chat:notification`(客服主动通知)。契约真源:`frontend/src/types/events.ts`。

## 数据库(10 表)

`products` · `customers` · `orders` · `conversations`(聊天记录持久化) · `agent_tasks`(任务审计) · `agent_memory`(预留) · `product_embeddings` · `faq_embeddings` · `market_embeddings`(pgvector) · `reply_templates`(客服话术模板)

## 环境配置

| 变量 | 用途 |
|------|------|
| `DB_HOST/PORT/USERNAME/PASSWORD/NAME` | PostgreSQL 连接 |
| `REDIS_HOST/PORT` | Redis 连接 |
| `LLM_API_KEY` | API Key |
| `LLM_API_URL` | API 端点 (如 `https://api.deepseek.com`) |
| `LLM_MODEL` | 模型名 (如 `deepseek-v4-flash`) |
| `EMBEDDING_API_URL` | Ollama 端点 (`http://localhost:11434`)，留空则用 OpenAI |
| `EMBEDDING_MODEL` | `bge-m3` (1024 维) 或 `text-embedding-3-small` (1536 维) |
| `EMBEDDING_DIMENSION` | 向量维度 (1024 或 1536) |

> ⚠️ Embedding 服务不可用时 `EmbeddingService` 显式报错(不静默降级为零向量)。

## 开发命令

```bash
# 后端 (cd python-backend)
uv run pytest                          # 全部测试 (含 WS e2e,需 Ollama/DeepSeek 在线)
uv run pytest -m "not e2e and not integration"   # CI 同款快速套件 (无 DB/外部依赖)
uv run alembic upgrade head            # 数据库迁移
uv run python -m python_backend.seed   # 数据播种 (幂等)
uv run ruff check .                    # Lint
uv run ruff format .                   # 格式化
uv run ty check .                      # 类型检查

# 前端 (cd frontend; 仓库根已无 package.json,npm 命令须在 frontend 下执行)
npm run lint / lint:fix                # ESLint 检查 / 自动修复
npm run format / format:check          # Prettier 格式化 / 只检查
npm run build                          # 前端构建 (tsc + vite)
npm run dev                            # Vite (5173)
```

> uv 不在 PATH：使用 `E:\Miniconda3\envs\uvProject\Scripts\uv.exe`(绝对路径)。PyPI 直连不畅时：`HTTPS_PROXY=http://127.0.0.1:7897 uv sync`。
> CI (`.github/workflows/ci.yml`)：push/PR 自动跑后端 ruff+ty+快速测试与前端 lint+build。

完整演示流程见 [docs/demo-script.md](docs/demo-script.md)；架构决策见 [docs/adr/](docs/adr/)。

## 新增 Agent

1. 在 `python-backend/src/python_backend/agents/<name>/` 下创建 `tools.py`，实现 `ITool` 接口（含 `definition` + `execute()`，需要 emit 事件时构造注入 `EventBus`）
2. 创建 `<name>/agent.py` 继承 `BaseAgent`，声明 `systemPrompt` + 工具清单（需要图级约束时声明 `workflow`）
3. 在 `main.py` 注册：`orchestrator.register_agent(agent, TaskType.XXX)`，并在组装处订阅需要响应的事件
