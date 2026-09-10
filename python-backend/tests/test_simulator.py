"""模拟流量生成器测试(spec #9 + spec #11):行为权重 / 预算保护 / 买家池 / 催单 / ops(httpx MockTransport)。"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from python_backend.simulator import ACTION_WEIGHTS, FOLLOW_UP_DELAY, Simulator, choose_action
from python_backend.simulator.scenarios import FOLLOW_UP_QUESTIONS, OPS_COMMANDS

PRODUCTS = [
    {"id": 1, "sku": "SKU-A", "title": "蓝牙音箱", "price": "19.99", "currency": "USD", "status": "active", "stock": 5},
    {"id": 2, "sku": "SKU-B", "title": "草稿品", "price": "9.99", "currency": "USD", "status": "draft", "stock": 3},
    {"id": 3, "sku": "SKU-C", "title": "无库存", "price": "9.99", "currency": "USD", "status": "active", "stock": 0},
]

BUYERS = [{"customerId": 5, "name": "张伟", "email": "zhangwei@example.com", "locale": "zh-CN"}]


def _make_simulator(
    *,
    rng: random.Random | None = None,
    daily_budget: int = 200,
    products=PRODUCTS,
    customers=None,
    include_ops: bool = False,
):
    calls: dict[str, list] = {"orders": [], "tasks": [], "sleeps": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/login":
            return httpx.Response(200, json={"token": "test-token", "username": "admin"})
        if path == "/api/products":
            return httpx.Response(200, json={"products": products})
        if path == "/api/customers":
            return httpx.Response(200, json={"customers": BUYERS if customers is None else customers})
        if path == "/api/orders":
            calls["orders"].append(json.loads(request.content))
            return httpx.Response(201, json={"order": {"id": 77, "status": "pending"}})
        if path == "/api/tasks":
            calls["tasks"].append(json.loads(request.content))
            return httpx.Response(201, json={"thread_id": "abcdef123456", "status": "completed"})
        raise AssertionError(f"未预期请求:{path}")

    async def fake_sleep(seconds: float) -> None:
        calls["sleeps"].append(seconds)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://sim")
    simulator = Simulator(
        "http://sim",
        "admin",
        "pw",
        rng=rng,
        client=client,
        daily_budget=daily_budget,
        include_ops=include_ops,
        sleep=fake_sleep,
    )
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
    """下单行为:走 POST /api/orders(真入口,零 LLM → 不计预算),只选在售且有库存的商品;带买家池身份。"""
    simulator, calls = _make_simulator(rng=random.Random(1))
    await simulator.login()
    result = await _run_action(simulator, "order")
    assert result["action"] == "order"
    assert calls["orders"] == [{"product_id": 1, "total_amount": "19.99", "currency": "USD", "customer_id": 5}]
    assert simulator.recent_order_ids == [77]
    # 下单本身零 LLM 不计预算;下单后催单计入(共 1)
    assert simulator.budget_spent == 1
    assert len(calls["tasks"]) == 1


async def test_order_falls_back_without_buyer_pool() -> None:
    """买家池为空:下单退化为不带买家(不报错,现行为)。"""
    simulator, calls = _make_simulator(rng=random.Random(1), customers=[])
    await simulator.login()
    result = await simulator._place_order()
    assert result["action"] == "order"
    assert calls["orders"][0] == {"product_id": 1, "total_amount": "19.99", "currency": "USD"}


async def test_follow_up_delayed_with_real_order_id() -> None:
    """催单(spec #11):延迟落在 30-90s 区间(时钟注入),引用真实订单号,计入预算。"""
    simulator, calls = _make_simulator(rng=random.Random(2))
    await simulator.login()
    await simulator._place_order()
    assert len(calls["sleeps"]) == 1
    assert FOLLOW_UP_DELAY[0] <= calls["sleeps"][0] <= FOLLOW_UP_DELAY[1]
    assert len(calls["tasks"]) == 1
    request_text = calls["tasks"][0]["request"]
    assert "#77" in request_text
    assert any(request_text == template.format(order_id=77) for template in FOLLOW_UP_QUESTIONS)


async def test_follow_up_skipped_when_budget_exhausted() -> None:
    """预算耗尽:下单照常(零 LLM),催单跳过——不等待、不烧 token。"""
    simulator, calls = _make_simulator(rng=random.Random(1), daily_budget=1)
    simulator.budget_spent = 1
    await simulator.login()
    await simulator._place_order()
    assert calls["orders"], "下单不受预算限制"
    assert calls["tasks"] == [] and calls["sleeps"] == []


class _ForcedRng(random.Random):
    """强制 random() 返回定值:确定性驱动「每轮 10% 概率」分支(权重选择随之为首个行为)。"""

    def __init__(self, value: float) -> None:
        super().__init__(0)
        self._value = value

    def random(self) -> float:
        return self._value


async def test_include_ops_dispatches_ops_command() -> None:
    """--include-ops 开启且概率命中:发运营分析指令(走 /api/tasks,计预算);关闭时不发。"""
    simulator, calls = _make_simulator(rng=_ForcedRng(0.0), include_ops=True)
    await simulator.login()
    result = await simulator.run_once()
    assert result["action"] == "browse"
    assert len(calls["tasks"]) == 1
    assert calls["tasks"][0]["request"] in OPS_COMMANDS
    assert simulator.budget_spent == 1

    quiet, quiet_calls = _make_simulator(rng=_ForcedRng(0.0), include_ops=False)
    await quiet.login()
    await quiet.run_once()
    assert quiet_calls["tasks"] == []


async def test_order_degrades_when_buyer_pool_query_fails() -> None:
    """买家池查询网络异常:下单照常(不带买家),不阻断本轮(spec #11 US 28)。"""
    calls: dict[str, list] = {"orders": [], "tasks": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/login":
            return httpx.Response(200, json={"token": "test-token", "username": "admin"})
        if path == "/api/products":
            return httpx.Response(200, json={"products": PRODUCTS})
        if path == "/api/customers":
            raise httpx.ConnectError("连接失败")
        if path == "/api/orders":
            calls["orders"].append(json.loads(request.content))
            return httpx.Response(201, json={"order": {"id": 77, "status": "pending"}})
        if path == "/api/tasks":
            calls["tasks"].append(json.loads(request.content))
            return httpx.Response(201, json={"thread_id": "abcdef123456", "status": "completed"})
        raise AssertionError(f"未预期请求:{path}")

    async def no_sleep(_seconds: float) -> None:
        return None

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://sim")
    simulator = Simulator("http://sim", "admin", "pw", rng=random.Random(1), client=client, sleep=no_sleep)
    await simulator.login()
    result = await simulator._place_order()
    assert result["action"] == "order"
    assert calls["orders"][0] == {"product_id": 1, "total_amount": "19.99", "currency": "USD"}


async def test_run_once_service_reuses_recent_order_id() -> None:
    """咨询行为:走 POST /api/tasks(计预算),订单号占位用本轮已下单的真实订单号。"""
    simulator, calls = _make_simulator(rng=random.Random(3))
    await simulator.login()
    await _run_action(simulator, "order")
    result = await _run_action(simulator, "service")
    assert result["action"] == "service"
    assert simulator.budget_spent == 2, "催单 + 咨询各计一次"
    task = calls["tasks"][-1]
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
