"""模拟流量生成器测试(spec #9):行为权重 / 预算保护 / 真入口调用(httpx MockTransport)。"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from python_backend.simulator import ACTION_WEIGHTS, Simulator, choose_action

PRODUCTS = [
    {"id": 1, "sku": "SKU-A", "title": "蓝牙音箱", "price": "19.99", "currency": "USD", "status": "active", "stock": 5},
    {"id": 2, "sku": "SKU-B", "title": "草稿品", "price": "9.99", "currency": "USD", "status": "draft", "stock": 3},
    {"id": 3, "sku": "SKU-C", "title": "无库存", "price": "9.99", "currency": "USD", "status": "active", "stock": 0},
]


def _make_simulator(*, rng: random.Random | None = None, daily_budget: int = 200, products=PRODUCTS):
    calls: dict[str, list] = {"orders": [], "tasks": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/login":
            return httpx.Response(200, json={"token": "test-token", "username": "admin"})
        if path == "/api/products":
            return httpx.Response(200, json={"products": products})
        if path == "/api/orders":
            calls["orders"].append(json.loads(request.content))
            return httpx.Response(201, json={"order": {"id": 77, "status": "pending"}})
        if path == "/api/tasks":
            calls["tasks"].append(json.loads(request.content))
            return httpx.Response(201, json={"thread_id": "abcdef123456", "status": "completed"})
        raise AssertionError(f"未预期请求:{path}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://sim")
    simulator = Simulator("http://sim", "admin", "pw", rng=rng, client=client, daily_budget=daily_budget)
    return simulator, calls


def test_choose_action_respects_budget_and_weights() -> None:
    """预算耗尽 → 只浏览;预算充足 → 命中权重表内的行为。"""
    rng = random.Random(7)
    assert choose_action(rng, 0) == "browse"
    assert choose_action(rng, -1) == "browse"
    picks = {choose_action(rng, 10) for _ in range(50)}
    assert picks <= {name for name, _weight in ACTION_WEIGHTS}
    assert picks, "预算充足时应能选出行为"


async def test_run_once_order_places_real_order() -> None:
    """下单行为:走 POST /api/orders(真入口,零 LLM → 不计预算),只选在售且有库存的商品。"""
    simulator, calls = _make_simulator(rng=random.Random(1))
    await simulator.login()
    result = await _run_action(simulator, "order")
    assert result["action"] == "order"
    assert calls["orders"] == [{"product_id": 1, "total_amount": "19.99", "currency": "USD"}]
    assert simulator.recent_order_ids == [77]
    assert simulator.budget_spent == 0, "下单不触发 LLM,不计入预算"


async def test_run_once_service_reuses_recent_order_id() -> None:
    """咨询行为:走 POST /api/tasks(计预算),订单号占位用本轮已下单的真实订单号。"""
    simulator, calls = _make_simulator(rng=random.Random(3))
    await simulator.login()
    await _run_action(simulator, "order")
    result = await _run_action(simulator, "service")
    assert result["action"] == "service"
    assert simulator.budget_spent == 1, "咨询触发 LLM,计入预算"
    task = calls["tasks"][0]
    assert task["session_id"].startswith("sim-")
    assert task["request"]
    assert "{order_id}" not in task["request"], "占位符应已用真实订单号填充"


async def test_budget_resets_on_new_day() -> None:
    """跨日重置:昨日已耗尽预算,今日恢复(预算保护不跨日累积)。"""
    simulator, _calls = _make_simulator(daily_budget=1)
    simulator.budget_spent = 1
    simulator.budget_date = datetime.now(UTC).date() - timedelta(days=1)
    assert simulator._budget_left() == 1


async def test_login_failure_is_loud() -> None:
    """登录失败如实抛错(不静默假装成功):凭据错误时模拟器立即停止。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "用户名或密码错误"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://sim")
    simulator = Simulator("http://sim", "admin", "wrong", client=client)
    with pytest.raises(RuntimeError, match="登录失败"):
        await simulator.login()
    await simulator.close()


async def _run_action(simulator: Simulator, action: str) -> dict:
    """驱动指定行为(绕过随机选择,直接调对应内部动作)。"""
    if action == "order":
        return await simulator._place_order()
    return await simulator._ask(["我的订单 #{order_id} 到哪里了?"], action)
