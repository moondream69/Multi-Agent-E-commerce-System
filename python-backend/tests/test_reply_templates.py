"""回复模板持久化单测:工具契约(find/fill/add)+ seed 幂等(需 docker Postgres,模式同 test_vector_search)。"""

import asyncio
from typing import cast

import pytest
from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from python_backend.agents.customer_service.tools import TemplateManagerTool
from python_backend.db.base import Base
from python_backend.db.models import ReplyTemplate
from python_backend.db.session import engine
from python_backend.seed import seed_templates

pytestmark = pytest.mark.integration





@pytest.fixture()
def prepared_db():
    Base.metadata.create_all(engine)  # 幂等:表已存在时无操作
    yield


@pytest.fixture()
def clean_test_rows(prepared_db):
    inserted: list[str] = []
    yield inserted
    with Session(engine) as session:
        table = cast(Table, ReplyTemplate.__table__)
        session.execute(table.delete().where(table.c.id.in_(inserted)))
        session.commit()


async def test_find_returns_contract_shape(prepared_db, clean_test_rows):
    with Session(engine) as session:
        session.add(
            ReplyTemplate(
                id="test_greeting",
                scenario="测试问候",
                template="您好",
                locale="zh-CN",
                variables=[],
            )
        )
        session.commit()
    clean_test_rows.append("test_greeting")

    result = await TemplateManagerTool().execute({"action": "find", "scenario": "测试问候", "locale": "zh-CN"})
    assert set(result.keys()) == {"id", "scenario", "template", "locale", "variables"}
    assert result["id"] == "test_greeting"


async def test_find_missing_returns_none(prepared_db, clean_test_rows):
    result = await TemplateManagerTool().execute({"action": "find", "scenario": "测试不存在", "locale": "zh-CN"})
    assert result is None


async def test_fill_substitutes_variables(prepared_db, clean_test_rows):
    with Session(engine) as session:
        session.add(
            ReplyTemplate(
                id="test_order_status",
                scenario="测试订单查询",
                template="您的订单 #{order_id} 当前状态为: {order_status}。预计{delivery_date}送达。",
                locale="zh-CN",
                variables=["order_id", "order_status", "delivery_date"],
            )
        )
        session.commit()
    clean_test_rows.append("test_order_status")

    result = await TemplateManagerTool().execute(
        {
            "action": "fill",
            "scenario": "测试订单查询",
            "locale": "zh-CN",
            "variables": {
                "order_id": "A100",
                "order_status": "已发货",
                "delivery_date": "9月10日",
            },
        }
    )
    assert result == "您的订单 #A100 当前状态为: 已发货。预计9月10日送达。"


async def test_fill_unknown_scenario_raises(prepared_db, clean_test_rows):
    with pytest.raises(ValueError, match="模板未找到"):
        await TemplateManagerTool().execute({"action": "fill", "scenario": "测试不存在"})


async def test_add_inserts_then_skips_duplicate_natural_key(prepared_db, clean_test_rows):
    tool = TemplateManagerTool()
    payload = {
        "action": "add",
        "template": {
            "id": "test_new_template",
            "scenario": "测试新场景",
            "template": "新模板正文",
            "locale": "zh-CN",
            "variables": [],
        },
    }
    first = await tool.execute(payload)
    second = await tool.execute(payload)  # 同 (scenario, locale) 重复新增

    assert first == {"success": True}
    assert second == {"success": True}  # 契约不变:skip 不暴露给 LLM
    with Session(engine) as session:
        rows = session.scalars(select(ReplyTemplate).where(ReplyTemplate.scenario == "测试新场景")).all()
        assert len(rows) == 1
    clean_test_rows.append("test_new_template")


def test_seed_templates_idempotent(prepared_db):
    # 重置 4 条规范行(与正式 seed 产物一致,留下无害)
    with Session(engine) as session:
        table = cast(Table, ReplyTemplate.__table__)
        session.execute(
            table.delete().where(table.c.id.in_(["greeting", "order_status", "return_policy", "escalation"]))
        )
        session.commit()

    assert seed_templates() == 4
    assert seed_templates() == 0  # 幂等:第二次全跳过

    with Session(engine) as session:
        assert len(session.scalars(select(ReplyTemplate)).all()) == 4


def test_execute_unknown_action_raises(prepared_db):
    with pytest.raises(ValueError, match="未知 action"):
        asyncio.run(TemplateManagerTool().execute({"action": "delete"}))
