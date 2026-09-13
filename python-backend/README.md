# python-backend

FastAPI + LangGraph 后端(唯一后端,端口 3000)。架构约束见仓库根 `docs/adr/0005-architecture-rebuild-production-charter.md`;完整开发命令与约定见根 `CLAUDE.md`。

## 启动与检查

```bash
uv run alembic upgrade head             # 迁移(13 业务表;checkpoint 表由 PostgresSaver 自建)
uv run python -m python_backend.run     # 启动:Windows 下经 run.py 建 SelectorEventLoop
                                        # (uvicorn 直启 main 会因 psycopg 不支持 Proactor 而失败)
uv run pytest                           # 全部测试(集成/e2e 需真实服务在线,离线秒 skip)
uv run pytest -m "not e2e and not integration"   # CI 同款快速套件
uv run ruff check . && uv run ty check .
```

PyPI 直连不畅时:`HTTPS_PROXY=http://127.0.0.1:7897 uv sync`。

## 结构

```
python-backend/
├─ src/python_backend/
│  ├─ run.py / main.py   启动入口(run.py 负责事件循环)/ FastAPI 装配 + 图与 Agent 装配
│  ├─ settings.py        配置(仓库根 .env 单一真源)
│  ├─ core/              规划 planning / 监督图 graph / 审批 approvals / 事件 events /
│  │                     通知 notifications / 记忆 memory / 导入 imports / 草案 drafting / 认证 auth
│  ├─ agents/            三个业务子图(product_research / order_management / customer_service)
│  │                     + executor(工具执行与审批批次 apply)+ registry(动作注册)
│  ├─ db/                SQLAlchemy 模型与存储(models / approval_store / report_store / ticket_store …)
│  ├─ api/               REST 与 socket.io 网关(app / ws)
│  ├─ infrastructure/    llm / embedding / tracing(Langfuse)/ fx(汇率)
│  ├─ simulator/         模拟流量(真 HTTP 入口)
│  └─ vector_repo/       向量访问抽象(Milvus 实现)
└─ alembic/              迁移(alembic/versions)
```
