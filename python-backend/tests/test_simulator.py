"""模拟流量生成器测试(纯单元:预算逻辑,不发起真实网络请求)。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from python_backend.simulator import Simulator


async def test_budget_reset_on_new_day():
    sim = Simulator("http://127.0.0.1:3000", "admin", "pw", daily_budget=5)
    sim.budget_spent = 5
    sim.budget_date = datetime.now(UTC).date() - timedelta(days=1)
    assert sim._budget_left() == 5  # 跨天重置
    assert sim.budget_spent == 0
    await sim.close()


async def test_budget_zero_blocks_llm_actions():
    sim = Simulator("http://127.0.0.1:3000", "admin", "pw", daily_budget=0)
    assert sim._budget_left() == 0
    # 预算为 0 时 LLM 类动作应直接跳过(ask_service 首行即返回)
    await sim.ask_service("测试")
    assert sim.budget_spent == 0
    await sim.close()


async def test_budget_consumed_by_llm_actions():
    sim = Simulator("http://127.0.0.1:3000", "admin", "pw", daily_budget=10)
    assert sim._budget_left() == 10
    sim.budget_spent = 9
    assert sim._budget_left() == 1
    await sim.close()
