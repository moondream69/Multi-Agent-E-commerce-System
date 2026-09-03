# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 开发命令

```bash
# 基础设施
docker compose up -d                                          # 启动 Postgres + Redis (compose 仅含这两项;embedding 由 Ollama 外部提供)

# 后端 (python-backend/,Python 版为唯一后端)
cd python-backend
uv run uvicorn python_backend.main:app --port 3000   # 启动(端口 3000,前端契约不变)
uv run pytest                                        # 全部测试(阶段 5 起含 WS e2e,需 Ollama/DeepSeek 在线)
uv run python -m python_backend.seed                 # 数据播种(幂等:按自然键跳过已存在记录)
uv run alembic upgrade head                          # 数据库迁移(9 表,含 pgvector 扩展)

# 代码检查与构建 (仓库根)
npm run lint                                                      # ESLint 检查 (前端,无 --fix)
npm run lint:fix                                                  # ESLint 自动修复 (安全+格式)
npm run format                                                    # Prettier 格式化 (前端)
npm run format:check                                              # Prettier 只检查
cd frontend && npm run build                                      # 前端构建 (tsc + vite)

# 前端 (另开终端)
cd frontend && npm run dev                                        # Vite (5173)
```

> ⚠️ `npm run lint` 只检查、不自动改写——需要自动修复时用 `npm run lint:fix`。
> lint/format 覆盖前端 (`frontend/src/`、`vite.config.ts`);prettier 选项唯一来源 `.prettierrc`。
> uv 不在 PATH:使用 `E:\Miniconda3\envs\uvProject\Scripts\uv.exe`(绝对路径)。PyPI 直连不畅时:
> `HTTPS_PROXY=http://127.0.0.1:7897 uv sync`。迁移后 launch 命令以 python-backend/README 为准。

## 技术栈

FastAPI + LangGraph · PostgreSQL 16 + pgvector (向量检索) · Redis 7 · DeepSeek v4 Flash (LLM) · 本地 BGE-M3 via Ollama (Embedding, 1024维) · React + Vite + socket.io-client · uv

## 核心架构

```
用户输入 (聊天 / REST) → IntentParser → Orchestrator → Agent.handleTask()
                                                           │
                                          BaseAgent (模板方法: 状态机 + 错误兜底)
                                                              │
                                          LangGraph StateGraph (ReAct 循环)
                                                              │
                                          LLM 选工具 → 工具执行 → 观察结果 → 再推理 → 输出
```

### 关键类与职责 (python-backend/src/python_backend/)

| 类 | 文件 | 职责 |
|-----|------|------|
| `BaseAgent` | `core/base_agent.py` | 模板方法：状态机 + `executeTask()` 调用 LangGraph 图。子类只需声明 `systemPrompt` + `tools[]` |
| ReAct 图 | `core/graph.py` | LangGraph 手绘 StateGraph：LLM 选工具 → 执行 → 观察 → 循环直到输出最终答案（最大 10 轮） |
| `Orchestrator` | `core/orchestrator.py` | Agent 注册 + 任务路由（按 `TaskType` 或显式指定目标 Agent） |
| `EventBus` | `core/event_bus.py` | Agent 间松耦合事件通信，`emit()` / `on()` / `broadcast()` |
| `LlmService` | `infrastructure/llm.py` | `complete()` (纯文本) + `completeWithTools()` (function calling)，通过 `LLM_API_URL` 配置端点 |
| `EmbeddingService` | `infrastructure/embedding.py` | `embed()`(→1024维向量) + `search()`(pgvector 余弦相似度) |
| `ITool` | `domain/tools.py` | `{ definition: ToolDefinition; execute(params): Promise<unknown> }` — 所有工具的标准化接口 |

### Agent 模式

三个 Agent 不再包含硬编码业务逻辑，只声明身份和工具清单，由 LLM 自主决定工具调用顺序：

```
ProductResearchAgent: systemPrompt + [trendQuery, competitorAnalysis, scoring, reportGenerator]
OrderManagementAgent: systemPrompt + [productCrud, orderWorkflow, inventoryAlert, anomalyDetection]
CustomerServiceAgent: systemPrompt + [translator, faqSearch, sentimentAnalysis, templateManager]
```

### 如何新增 Agent

1. 在 `python-backend/src/python_backend/agents/<name>/` 下创建 `tools.py`，实现 `ITool` 接口（含 `definition` + `execute()`）
2. 创建 `<name>/agent.py` 继承 `BaseAgent`，声明 `systemPrompt` + 工具清单
3. 在 `main.py` 中注册：`orchestrator.register_agent(agent, TaskType.XXX)`

## 环境配置

| 变量 | 用途 |
|------|------|
| `DB_HOST/PORT/USERNAME/PASSWORD/NAME` | PostgreSQL 连接 |
| `REDIS_HOST/PORT` | Redis 连接 |
| `LLM_API_KEY` | API Key |
| `LLM_API_URL` | API 端点 (如 `https://api.deepseek.com`) |
| `LLM_MODEL` | 模型名 (如 `deepseek-v4-flash`) |
| `EMBEDDING_API_URL` | Ollama 端点 (`http://localhost:11434`)，留空则用 OpenAI |
| `EMBEDDING_MODEL` | `bge-m3` (1024维) 或 `text-embedding-3-small` (1536维) |
| `EMBEDDING_DIMENSION` | 向量维度 (1024 或 1536) |

> ⚠️ Embedding 服务不可用时 `EmbeddingService` 显式报错(不静默降级为零向量)。
> 前端类型是 API 契约唯一真源:`frontend/src/types/events.ts`(对应契约测试 `python-backend/tests/test_contract.py`)。
