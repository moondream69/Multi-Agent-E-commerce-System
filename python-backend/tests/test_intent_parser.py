"""意图解析:关键词规则与兜底(TS 侧无 spec,按 same 逻辑补全)。"""

from python_backend.core.intent_parser import IntentParser
from python_backend.domain.tasks import TaskType

parser = IntentParser()


def test_product_research_keyword():
    result = parser.parse("帮我分析一下市场趋势和竞品")
    assert result.task_type == TaskType.PRODUCT_RESEARCH
    assert result.extracted_input == {"action": "analyze", "query": "帮我分析一下市场趋势和竞品"}


def test_order_management_keyword():
    result = parser.parse("这个订单什么时候发货")
    assert result.task_type == TaskType.ORDER_MANAGEMENT
    assert result.extracted_input["action"] == "create_product"


def test_customer_service_keyword():
    result = parser.parse("客户投诉了,帮我翻译一下回复")
    assert result.task_type == TaskType.CUSTOMER_SERVICE
    assert result.extracted_input["action"] == "handle_query"


def test_fallback_to_customer_service():
    result = parser.parse("今天天气怎么样")
    assert result.task_type == TaskType.CUSTOMER_SERVICE
    assert result.extracted_input == {"action": "handle_query", "text": "今天天气怎么样"}


def test_mixed_keywords_score_by_hits():
    """回归:多类关键词命中时按命中数取分,而不是首个顺序命中(NestJS 版误路由案例)。"""
    result = parser.parse("客户抱怨物流太慢,帮我写个安抚回复")
    assert result.task_type == TaskType.CUSTOMER_SERVICE
    assert result.extracted_input["action"] == "handle_query"


def test_order_keywords_still_route_to_order():
    result = parser.parse("订单物流延迟了,帮我追踪一下")
    assert result.task_type == TaskType.ORDER_MANAGEMENT
