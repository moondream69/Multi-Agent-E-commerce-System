"""组装器:事件总线 → Agent 注册 → REST 路由 → WebSocket 网关(端口 3000,契约不变)。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import socketio
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from python_backend.agents.customer_service.agent import CustomerServiceAgent
from python_backend.agents.customer_service.tools import (
    EscalateTicketTool,
    FaqRetrievalTool,
    OrderLookupTool,
    SentimentAnalysisTool,
    TemplateManagerTool,
    TranslatorTool,
)
from python_backend.agents.order_management.agent import OrderManagementAgent
from python_backend.agents.order_management.tools import (
    AnomalyDetectionTool,
    InventoryAlertTool,
    OrderWorkflowTool,
    ProductCrudTool,
)
from python_backend.agents.product_research.agent import ProductResearchAgent
from python_backend.agents.product_research.tools import (
    CompetitorAnalysisTool,
    ReportGeneratorTool,
    ScoringTool,
    TrendQueryTool,
)
from python_backend.api.approvals import build_approvals_router
from python_backend.api.auth import build_auth_router, require_user
from python_backend.api.rest import build_router
from python_backend.api.store import build_store_router
from python_backend.api.ws import (
    bridge_all_events,
    bridge_notifications,
    register_ws_handlers,
)
from python_backend.core.approval import register_tool
from python_backend.core.event_bus import EventBus
from python_backend.core.intent_parser import IntentParser
from python_backend.core.orchestrator import Orchestrator
from python_backend.db import approval_repo
from python_backend.domain.events import AgentEventType
from python_backend.domain.tasks import TaskType
from python_backend.infrastructure.llm import LlmService
from python_backend.settings import settings


def build_real_tools(llm: LlmService, event_bus: EventBus | None = None) -> dict:
    return {
        "research": (
            TrendQueryTool(),
            CompetitorAnalysisTool(),
            ScoringTool(),
            ReportGeneratorTool(event_bus),
        ),
        "order": (
            ProductCrudTool(event_bus),
            OrderWorkflowTool(event_bus),
            InventoryAlertTool(event_bus),
            AnomalyDetectionTool(),
        ),
        "service": (
            TranslatorTool(llm),
            FaqRetrievalTool(),
            SentimentAnalysisTool(llm),
            TemplateManagerTool(event_bus),
            OrderLookupTool(),
            EscalateTicketTool(event_bus),
        ),
    }


def create_app(orchestrator: Orchestrator | None = None) -> FastAPI:
    """组装配齐 REST + WS 的 ASGI 应用。

    orchestrator 为 None 时注册真实三 Agent;测试可注入带桩 Agent 的编排器。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 启动清扫:进程重启丢在途审批,陈旧 pending 置 expired(卖家重发指令即可)
        expired = approval_repo.expire_stale(settings.approval_ttl_hours)
        if expired:
            print(f"[approval] 启动清扫: {expired} 条过期审批已置 expired", flush=True)
        yield

    http_app = FastAPI(title="multi-agent-ecommerce", version="0.1.0", lifespan=lifespan)
    http_app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")

    if orchestrator is None:
        event_bus = EventBus()
        llm = LlmService()
        real_tools = build_real_tools(llm, event_bus)

        # 工具实例注册进全局注册表(影子建议补执行 + 审计用)
        for group in real_tools.values():
            for tool in group:
                register_tool(tool)

        research_agent = ProductResearchAgent(event_bus, llm, *real_tools["research"])
        order_agent = OrderManagementAgent(event_bus, llm, *real_tools["order"])
        service_agent = CustomerServiceAgent(event_bus, llm, *real_tools["service"])

        orchestrator = Orchestrator(event_bus)
        orchestrator.register_agent(research_agent, TaskType.PRODUCT_RESEARCH)
        orchestrator.register_agent(order_agent, TaskType.ORDER_MANAGEMENT)
        orchestrator.register_agent(service_agent, TaskType.CUSTOMER_SERVICE)

        # 跨 Agent 事件订阅(后台数据流):选品报告 → 订单 Agent 自动建草稿;
        # 订单状态/库存告警 → 客服 Agent 生成主动通知
        event_bus.on(AgentEventType.REPORT_GENERATED, order_agent.handle_event)
        event_bus.on(AgentEventType.ORDER_STATUS_CHANGED, service_agent.handle_event)
        event_bus.on(AgentEventType.INVENTORY_ALERT, service_agent.handle_event)
    else:
        event_bus = orchestrator._event_bus

    register_ws_handlers(sio, orchestrator, IntentParser())
    bridge_all_events(sio, event_bus)
    bridge_notifications(sio, event_bus)
    http_app.include_router(build_auth_router())
    # 业务路由整体加认证(内部工具,store API 保留为模拟流量入口,同样需 token)
    http_app.include_router(build_router(orchestrator), dependencies=[Depends(require_user)])
    http_app.include_router(build_store_router(event_bus), dependencies=[Depends(require_user)])
    http_app.include_router(build_approvals_router(event_bus), dependencies=[Depends(require_user)])

    @http_app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    # 生产:前端构建产物由本进程静态托管(单端口 3000,同源部署,CORS 不再跨域);
    # 开发/测试(无 dist):保留 JSON 根路由,契约测试锚定
    dist_dir = Path(__file__).resolve().parents[3] / "dist"
    if dist_dir.is_dir():
        http_app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="assets")

        @http_app.get("/")
        def index() -> FileResponse:
            return FileResponse(dist_dir / "index.html")

        @http_app.get("/{path:path}")
        def spa_fallback(path: str) -> FileResponse:
            """SPA 回退:非 API/非静态路径一律返回 index.html(前端无路由库,防刷新 404)。"""
            return FileResponse(dist_dir / "index.html")
    else:

        @http_app.get("/")
        def root() -> dict:
            return {"message": "Hello World!"}

    # 作为 uvicorn 入口的 ASGI 应用
    http_app.state.asgi = socketio.ASGIApp(sio, other_asgi_app=http_app)
    return http_app


def make_asgi() -> socketio.ASGIApp:
    return create_app().state.asgi
