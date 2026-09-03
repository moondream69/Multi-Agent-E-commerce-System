# Multi-Agent E-commerce System

多 AI Agent 协作的跨境电商系统，面向中国出海电商场景。采用 LLM 驱动的 ReAct 推理模式，三个 Agent 各自携带专用工具，由大模型自主决策工具调用顺序。

## 技术栈

FastAPI + LangGraph · PostgreSQL 16 + pgvector (向量检索) · Redis 7 · DeepSeek v4 Flash (LLM) · 本地 BGE-M3 via Ollama (Embedding, 1024 维) · React + Vite + socket.io-client · uv

## 快速开始

```bash
cp .env.example .env                    # 编辑 .env 填入实际配置 (EMBEDDING_API_URL 用 Ollama:11434)
docker compose up -d                    # 启动 PostgreSQL + Redis
# Embedding 由本机 Ollama (http://localhost:11434, 模型 bge-m3) 提供——先 `ollama pull bge-m3` 确认模型已拉取

cd python-backend
uv run alembic upgrade head             # 数据库迁移 (9 表,含 pgvector 扩展)
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

### 关键组件

| 组件 | 职责 |
|------|------|
| `BaseAgent` (`core/base_agent.py`) | 模板方法：状态机 + `executeTask()` 调用 LangGraph 图 |
| `ReAct 图` (`core/graph.py`) | LangGraph 手绘 StateGraph：LLM 选工具 → 执行 → 观察 → 循环直到输出最终答案 |
| `Orchestrator` (`core/orchestrator.py`) | Agent 注册 + 任务路由（按 `TaskType` 或显式指定目标 Agent） |
| `EventBus` (`core/event_bus.py`) | Agent 间松耦合事件通信，`emit()` / `on()` / `broadcast()` |
| `LlmService` (`infrastructure/llm.py`) | `complete()` (纯文本) + `completeWithTools()` (function calling) |
| `EmbeddingService` (`infrastructure/embedding.py`) | `embed()` (→1024 维向量) + `search()` (pgvector 余弦相似度) |
| `ITool` (`domain/tools.py`) | 所有工具的标准化接口：`{ definition; execute(params) }` |

### Agent 清单

三个 Agent 不写死业务逻辑，只声明身份和工具清单，由 LLM 自主决定工具调用顺序：

| Agent | 工具 |
|-------|------|
| **ProductResearchAgent** | `trendQuery`, `competitorAnalysis`, `scoring`, `reportGenerator` |
| **OrderManagementAgent** | `productCrud`, `orderWorkflow`, `inventoryAlert`, `anomalyDetection` |
| **CustomerServiceAgent** | `translator`, `faqSearch`, `sentimentAnalysis`, `templateManager` |

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
uv run alembic upgrade head            # 数据库迁移
uv run python -m python_backend.seed   # 数据播种 (幂等)

# 前端与代码检查 (仓库根)
npm run lint                           # ESLint 检查 (前端; 不自动改写)
npm run lint:fix                       # ESLint 自动修复 (安全+格式)
npm run format                         # Prettier 格式化
npm run format:check                   # Prettier 只检查
cd frontend && npm run build           # 前端构建 (tsc + vite)

# 前端开发 (另开终端)
cd frontend && npm run dev             # Vite (5173)
```

> uv 不在 PATH：使用 `E:\Miniconda3\envs\uvProject\Scripts\uv.exe`(绝对路径)。PyPI 直连不畅时：`HTTPS_PROXY=http://127.0.0.1:7897 uv sync`。

## 新增 Agent

1. 在 `python-backend/src/python_backend/agents/<name>/` 下创建 `tools.py`，实现 `ITool` 接口（含 `definition` + `execute()`）
2. 创建 `<name>/agent.py` 继承 `BaseAgent`，声明 `systemPrompt` + 工具清单
3. 在 `main.py` 注册：`orchestrator.register_agent(agent, TaskType.XXX)`
