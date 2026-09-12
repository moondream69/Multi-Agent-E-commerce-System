"""模拟流量生成器(spec #9 + spec #11):持续产生逼真买家行为,驱动系统在真实感数据流上运行。

用法(需后端已启动并完成 seed/CSV 导入):
    uv run python -m python_backend.simulator --once                      # 冒烟:执行一轮
    uv run python -m python_backend.simulator --loop 300                  # 每 300 秒一轮,直到 Ctrl+C
    uv run python -m python_backend.simulator --loop 300 --include-ops    # 附加运营分析指令(10% 概率)
    uv run python -m python_backend.simulator --loop 300 --daily-budget 200

全部走 HTTP 真入口(登录拿 JWT → 调 REST/任务端点),不直写业务表:
下单经 POST /api/orders(零 LLM:扣真实库存、落汇率快照、触发通知;买家池来自 GET /api/customers,
池空退化为不带买家),咨询/投诉/催单/运营指令经 POST /api/tasks(触发 Manager 规划与业务子图)。
LLM 预算:仅触发 LLM 的动作(咨询/投诉/催单/运营指令)消耗预算,达预算后当天剩余轮次只做浏览。
下单后 30-90s 发一条催单(引用真实订单号)——--once 冒烟若命中下单会等待该延迟。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx

from python_backend.settings import get_settings
from python_backend.simulator.scenarios import (
    COMPLAINTS,
    FOLLOW_UP_QUESTIONS,
    OPS_COMMANDS,
    SERVICE_QUESTIONS,
)

logger = logging.getLogger("simulator")

# 行为权重:(名称, 权重)。浏览不计 LLM 预算。
ACTION_WEIGHTS: list[tuple[str, int]] = [("browse", 5), ("order", 40), ("service", 35), ("complain", 10)]

# 催单延迟区间(秒):下单后等待片刻再追问,模拟真实买家行为(测试注入时钟)
FOLLOW_UP_DELAY: tuple[int, int] = (30, 90)

# 运营指令概率(--include-ops 开启后每轮):分析类指令烧 token 较多,低概率混发
OPS_PROBABILITY = 0.1

# HTTP 客户端超时(秒):须覆盖同步端点 POST /api/tasks 的真实耗时(图跑完才响应,慢响应可超 30s)
CLIENT_TIMEOUT = 120.0


def choose_action(rng: random.Random, budget_left: int) -> str:
    """按权重选择本轮行为;预算耗尽 → 只浏览(browse 不消耗 LLM)。"""
    if budget_left <= 0:
        return "browse"
    names = [name for name, _weight in ACTION_WEIGHTS]
    weights = [weight for _name, weight in ACTION_WEIGHTS]
    return rng.choices(names, weights=weights, k=1)[0]


class Simulator:
    """模拟买家:登录 → 每轮按权重行动;买家池来自 DB(空池退化),下单后延迟催单。"""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        daily_budget: int = 200,
        include_ops: bool = False,
        rng: random.Random | None = None,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.daily_budget = daily_budget
        self.include_ops = include_ops
        self.budget_date = datetime.now(UTC).date()
        self.budget_spent = 0
        self.recent_order_ids: list[int] = []
        self._rng = rng or random.Random()
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=CLIENT_TIMEOUT)
        self._sleep = sleep or asyncio.sleep  # 催单延迟的时钟(测试注入,免真实等待)

    async def close(self) -> None:
        await self._client.aclose()

    async def login(self) -> None:
        response = await self._client.post(
            "/api/auth/login", json={"username": self.username, "password": self.password}
        )
        if response.status_code != 200:
            raise RuntimeError(f"登录失败({response.status_code}):{response.text[:200]}")
        self._client.headers["Authorization"] = f"Bearer {response.json()['token']}"

    def _budget_left(self) -> int:
        """跨日重置预算(UTC 日期翻转)。"""
        today = datetime.now(UTC).date()
        if today != self.budget_date:
            self.budget_date, self.budget_spent = today, 0
        return self.daily_budget - self.budget_spent

    async def _products(self) -> list[dict]:
        response = await self._client.get("/api/products")
        response.raise_for_status()
        return response.json()["products"]

    async def _customers(self) -> list[dict]:
        """买家池(spec #11):查询失败(网络异常/非 200)→ 空池(下单退化为不带买家,不阻断本轮)。"""
        try:
            response = await self._client.get("/api/customers")
        except httpx.HTTPError as error:
            logger.warning("买家池查询异常(%s),本轮下单不带买家", error)
            return []
        if response.status_code != 200:
            logger.warning("买家池查询失败(%s),本轮下单不带买家", response.status_code)
            return []
        return response.json()["customers"]

    async def run_once(self) -> dict:
        """执行一轮行为,返回 {action, detail}(供日志与测试断言);--include-ops 追加运营指令。"""
        action = choose_action(self._rng, self._budget_left())
        if action == "browse":
            products = await self._products()
            result = {"action": "browse", "detail": f"{len(products)} 个商品"}
        elif action == "order":
            result = await self._place_order()
        else:
            template_pool = COMPLAINTS if action == "complain" else SERVICE_QUESTIONS
            result = await self._ask(template_pool, action)
        # 运营指令(spec #11):开启后每轮 10% 概率,计入 LLM 预算
        if self.include_ops and self._budget_left() > 0 and self._rng.random() < OPS_PROBABILITY:
            ops = await self._ask(OPS_COMMANDS, "ops")
            logger.info("运营指令:%s", ops["detail"])
        return result

    async def _place_order(self) -> dict:
        products = [
            product for product in await self._products() if product["status"] == "active" and product["stock"] > 0
        ]
        if not products:
            logger.warning("无在售且有库存的商品,本轮改浏览")
            return {"action": "browse", "detail": "无可下单商品"}
        product = self._rng.choice(products)
        customers = await self._customers()
        customer = self._rng.choice(customers) if customers else None
        payload = {"product_id": product["id"], "total_amount": product["price"], "currency": product["currency"]}
        if customer is not None:
            payload["customer_id"] = customer["customerId"]
        response = await self._client.post("/api/orders", json=payload)
        # 下单是零 LLM 的直连入口,不计入 LLM 预算(预算只约束触发 LLM 的任务)
        if response.status_code != 201:
            logger.warning("下单失败(%s):%s", response.status_code, response.text[:200])
            return {"action": "order", "detail": f"失败 {response.status_code}"}
        order_id = response.json()["order"]["id"]
        self.recent_order_ids = [*self.recent_order_ids[-9:], order_id]
        note = f"(买家 {customer['name']})" if customer is not None else ""
        follow_up = await self._follow_up(order_id)
        suffix = ";催单已发" if follow_up is not None else ""
        return {"action": "order", "detail": f"订单 #{order_id} {product['sku']}{note}{suffix}"}

    async def _follow_up(self, order_id: int) -> dict | None:
        """下单后催单(spec #11):延迟 30-90s 引真实订单号追问;预算耗尽则跳过(不烧 token)。"""
        if self._budget_left() <= 0:
            return None
        await self._sleep(self._rng.uniform(*FOLLOW_UP_DELAY))
        question = self._rng.choice(FOLLOW_UP_QUESTIONS).format(order_id=order_id)
        return await self._ask([question], "follow_up")

    async def _ask(self, templates: list[str], action: str) -> dict:
        # 无近期订单时避开 {order_id} 模板(不留「订单 #0」这种无意义占位)
        pool = (
            [template for template in templates if "{order_id}" not in template] or templates
            if not self.recent_order_ids
            else templates
        )
        template = self._rng.choice(pool)
        order_id = self._rng.choice(self.recent_order_ids) if self.recent_order_ids else 0
        text = template.format(order_id=order_id) if "{order_id}" in template else template
        response = await self._client.post(
            "/api/tasks", json={"request": text, "session_id": f"sim-{self.budget_date.isoformat()}"}
        )
        self.budget_spent += 1
        if response.status_code != 201:
            logger.warning("任务发起失败(%s):%s", response.status_code, response.text[:200])
            return {"action": action, "detail": f"失败 {response.status_code}"}
        return {"action": action, "detail": f"任务 {response.json()['threadId'][:8]}:{text[:20]}"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模拟流量生成器(spec #9 + spec #11)")
    parser.add_argument("--base-url", default="http://localhost:3000", help="后端地址")
    parser.add_argument("--once", action="store_true", help="执行一轮后退出(冒烟)")
    parser.add_argument("--loop", type=int, metavar="SECONDS", help="每 N 秒一轮,直到 Ctrl+C")
    parser.add_argument("--daily-budget", type=int, default=200, help="每日 LLM 消耗动作上限(默认 200)")
    parser.add_argument("--include-ops", action="store_true", help="附加运营分析指令(每轮 10% 概率,烧 token 较多)")
    return parser


async def _run(simulator: Simulator, args: argparse.Namespace) -> int:
    await simulator.login()
    while True:
        result = await simulator.run_once()
        logger.info(
            "本轮行为:%s(%s) 预算 %s/%s",
            result["action"],
            result["detail"],
            simulator.budget_spent,
            simulator.daily_budget,
        )
        if args.once or args.loop is None:
            return 0
        await asyncio.sleep(args.loop)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    settings = get_settings()
    simulator = Simulator(
        args.base_url,
        settings.auth_admin_username,
        settings.auth_admin_password,
        daily_budget=args.daily_budget,
        include_ops=args.include_ops,
    )
    try:
        return asyncio.run(_run(simulator, args))
    finally:
        asyncio.run(simulator.close())
