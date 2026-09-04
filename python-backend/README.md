# python-backend

由 NestJS 版后端迁移而来的 Python 单服务(FastAPI + LangGraph),端口 3000,前端契约不变。
迁移原因与约束见仓库根 `docs/adr/0001-backend-python-langgraph-migration.md`。

## 启动

```bash
cd python-backend
uv run uvicorn python_backend.main:app --port 3000   # 依赖:docker compose up -d + Ollama(bge-m3) + DeepSeek
```

> uv 不在 PATH,使用 `E:\Miniconda3\envs\uvProject\Scripts\uv.exe`(绝对路径)。
> PyPI 直连不畅时: `HTTPS_PROXY=http://127.0.0.1:7897 uv sync`。

## 常用命令

```bash
uv run pytest                    # 全部测试(WS e2e 需要 Ollama/DeepSeek 在线)
uv run pytest -m "not e2e and not integration"   # CI 同款快速套件
uv run alembic upgrade head      # 数据库迁移(10 表 + pgvector 扩展)
uv run python -m python_backend.seed   # 数据播种(幂等:按自然键跳过已存在记录)
uv run ruff check .              # Lint
uv run ty check .                # 类型检查
```

## 结构

```
src/python_backend/
├─ main.py           uvicorn 入口(python_backend.main:app)
├─ settings.py       配置(.env 在仓库根,变量名与 NestJS 版一致)
├─ domain/           领域类型(Task/Agent/Event/Tool,镜像 TS 的 common/interfaces)
├─ core/
│  ├─ event_bus.py   进程内异步事件总线
│  ├─ intent_parser.py  关键词规则意图解析
│  ├─ orchestrator.py   任务路由 + 生命周期事件
│  ├─ graph.py       LangGraph 状态图实现的 ReAct 循环(10 轮上限/错误兜底)
│  └─ base_agent.py  模板方法:状态机(idle/busy/error/offline)+ 步骤记录
├─ agents/           3 个 Agent(systemPrompt + 12 个工具)
├─ infrastructure/   llm(ChatOpenAI→DeepSeek + Redis 缓存)、embedding(Ollama,失败显式报错)
├─ api/              REST 路由(agents/dashboard/store)、socketio 网关、Pydantic 校验、契约序列化
├─ db/               SQLAlchemy 模型 / session / pgvector 检索 / conversation+agent_task 持久化
├─ seed.py           幂等数据播种
└─ alembic/          Alembic 迁移(初始迁移 + pgvector 扩展)
```
