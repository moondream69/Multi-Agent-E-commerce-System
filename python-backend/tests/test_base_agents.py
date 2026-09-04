"""三个 Agent 的 spec 迁移(1:1 对应 product-research/order-management/customer-service.agent.spec.ts)。

handleTask 通过替换 execute_task 为假实现(对应 TS spec 中 mock ReActLoop)。
"""

from __future__ import annotations

import pytest

from python_backend.agents.customer_service.agent import CustomerServiceAgent
from python_backend.agents.customer_service.tools import (
    FaqRetrievalTool,
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
from python_backend.core.event_bus import EventBus
from python_backend.domain.agents import AgentStatus
from python_backend.domain.events import AgentEventType
from python_backend.domain.tasks import AgentTask, TaskStatus, TaskType
from python_backend.infrastructure.llm import LlmService

LLM = LlmService()
EVENTS: list = []
BUS = EventBus()
BUS.on(AgentEventType.AGENT_STATUS_CHANGED, lambda e: EVENTS.append(e.payload))


def _task(task_type: TaskType, input_: dict) -> AgentTask:
    return AgentTask(id="t1", type=task_type, input=input_)


@pytest.fixture(autouse=True)
def _reset_events():
    EVENTS.clear()
    yield


async def _patch_execute(agent, output: dict):
    async def fake_execute(task):
        return output

    agent.execute_task = fake_execute  # type: ignore[method-assign]
    return agent


async def test_product_research_agent_basics():
    agent = ProductResearchAgent(
        BUS, LLM, TrendQueryTool(), CompetitorAnalysisTool(), ScoringTool(), ReportGeneratorTool()
    )
    assert agent.id == "product-research"
    assert agent.name == "选品分析Agent"
    assert agent.system_prompt
    assert len(agent.get_tools()) == 4


async def test_product_research_handle_task_and_status_events():
    agent = ProductResearchAgent(
        BUS, LLM, TrendQueryTool(), CompetitorAnalysisTool(), ScoringTool(), ReportGeneratorTool()
    )
    await _patch_execute(agent, {"result": "test output"})
    result = await agent.handle_task(_task(TaskType.PRODUCT_RESEARCH, {"query": "test"}))
    assert result.status == TaskStatus.COMPLETED
    assert any(p["status"] == AgentStatus.BUSY.value and p["taskId"] == "t1" for p in EVENTS)
    assert any(p["status"] == AgentStatus.IDLE.value and p["taskId"] == "t1" for p in EVENTS)


async def test_order_management_agent_basics():
    agent = OrderManagementAgent(
        BUS,
        LLM,
        ProductCrudTool(),
        OrderWorkflowTool(),
        InventoryAlertTool(),
        AnomalyDetectionTool(),
    )
    assert agent.id == "order-management"
    assert agent.name == "订单处理Agent"
    assert agent.system_prompt
    assert len(agent.get_tools()) == 4


async def test_order_management_handle_task():
    agent = OrderManagementAgent(
        BUS,
        LLM,
        ProductCrudTool(),
        OrderWorkflowTool(),
        InventoryAlertTool(),
        AnomalyDetectionTool(),
    )
    await _patch_execute(agent, {"result": "test output"})
    result = await agent.handle_task(
        _task(
            TaskType.ORDER_MANAGEMENT,
            {
                "action": "check_inventory",
                "productName": "蓝牙耳机",
                "currentStock": 20,
                "threshold": 100,
            },
        )
    )
    assert result.status == TaskStatus.COMPLETED


async def test_customer_service_agent_basics():
    agent = CustomerServiceAgent(
        BUS,
        LLM,
        TranslatorTool(LLM),
        FaqRetrievalTool(),
        SentimentAnalysisTool(LLM),
        TemplateManagerTool(),
    )
    assert agent.id == "customer-service"
    assert agent.name == "客服Agent"
    assert agent.system_prompt
    assert len(agent.get_tools()) == 4


async def test_customer_service_handle_task():
    agent = CustomerServiceAgent(
        BUS,
        LLM,
        TranslatorTool(LLM),
        FaqRetrievalTool(),
        SentimentAnalysisTool(LLM),
        TemplateManagerTool(),
    )
    await _patch_execute(agent, {"result": "test output"})
    result = await agent.handle_task(_task(TaskType.CUSTOMER_SERVICE, {"action": "handle_query", "text": "如何退货?"}))
    assert result.status == TaskStatus.COMPLETED


async def test_customer_service_agent_declares_workflow():
    agent = CustomerServiceAgent(
        BUS,
        LLM,
        TranslatorTool(LLM),
        FaqRetrievalTool(),
        SentimentAnalysisTool(LLM),
        TemplateManagerTool(),
    )
    assert agent.workflow is not None
    assert agent.workflow.phases[0].required_tools == {"sentiment_analysis", "faq_search"}
    assert agent.workflow.phases[1].can_answer is True


async def test_other_agents_have_no_workflow():
    research = ProductResearchAgent(
        BUS, LLM, TrendQueryTool(), CompetitorAnalysisTool(), ScoringTool(), ReportGeneratorTool()
    )
    order = OrderManagementAgent(
        BUS,
        LLM,
        ProductCrudTool(),
        OrderWorkflowTool(),
        InventoryAlertTool(),
        AnomalyDetectionTool(),
    )
    assert research.workflow is None
    assert order.workflow is None
