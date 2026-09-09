"""模拟流量生成器(spec #9):持续产生逼真买家行为,驱动系统在真实感数据流上运行。

用法(需后端已启动并完成 seed/CSV 导入):
    uv run python -m python_backend.simulator --once                      # 冒烟:执行一轮
    uv run python -m python_backend.simulator --loop 300                  # 每 300 秒一轮,直到 Ctrl+C
    uv run python -m python_backend.simulator --loop 300 --daily-budget 200

全部走 HTTP 真入口(登录拿 JWT → 调 REST/任务端点),不直写业务表:
下单经 POST /api/orders(零 LLM:扣真实库存、落汇率快照、触发通知),
咨询/投诉经 POST /api/tasks(触发 Manager 规划与业务子图,计入 LLM 预算)。
LLM 预算:仅咨询/投诉(触发 LLM)消耗预算,达预算后当天剩余轮次只做浏览(不计预算)。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
from datetime import UTC, datetime

import httpx

from python_backend.settings import get_settings
from python_backend.simulator.scenarios import COMPLAINTS, SERVICE_QUESTIONS

logger = logging.getLogger("simulator")

# 行为权重:(名称, 权重)。浏览不计 LLM 预算。
ACTION_WEIGHTS: list[tuple[str, int]] = [("browse", 5), ("order", 40), ("service", 35), ("complain", 10)]


def choose_action(rng: random.Random, budget_left: int) -> str:
    """按权重选择本轮行为;预算耗尽 → 只浏览(browse 不消耗 LLM)。"""
    if budget_left <= 0:
        return "browse"
    names = [name for name, _weight in ACTION_WEIGHTS]
    weights = [weight for _name, weight in ACTION_WEIGHTS]
    return rng.choices(names, weights=weights, k=1)[0]


class Simulator:
    """模拟买家:登录 → 每轮按权重行动;记录本轮真实订单号供咨询/投诉引用。"""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        daily_budget: int = 200,
        rng: random.Random | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.daily_budget = daily_budget
        self.budget_date = datetime.now(UTC).date()
        self.budget_spent = 0
        self.recent_order_ids: list[int] = []
        self._rng = rng or random.Random()
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

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

    async def run_once(self) -> dict:
        """执行一轮行为,返回 {action, detail}(供日志与测试断言)。"""
        action = choose_action(self._rng, self._budget_left())
        if action == "browse":
            products = await self._products()
            return {"action": "browse", "detail": f"{len(products)} 个商品"}
        if action == "order":
            return await self._place_order()
        template_pool = COMPLAINTS if action == "complain" else SERVICE_QUESTIONS
        return await self._ask(template_pool, action)

    async def _place_order(self) -> dict:
        products = [
            product for product in await self._products() if product["status"] == "active" and product["stock"] > 0
        ]
        if not products:
            logger.warning("无在售且有库存的商品,本轮改浏览")
            return {"action": "browse", "detail": "无可下单商品"}
        product = self._rng.choice(products)
        response = await self._client.post(
            "/api/orders",
            json={"product_id": product["id"], "total_amount": product["price"], "currency": product["currency"]},
        )
        # 下单是零 LLM 的直连入口,不计入 LLM 预算(预算只约束触发 LLM 的任务)
        if response.status_code != 201:
            logger.warning("下单失败(%s):%s", response.status_code, response.text[:200])
            return {"action": "order", "detail": f"失败 {response.status_code}"}
        order_id = response.json()["order"]["id"]
        self.recent_order_ids = [*self.recent_order_ids[-9:], order_id]
        return {"action": "order", "detail": f"订单 #{order_id} {product['sku']}"}

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
        return {"action": action, "detail": f"任务 {response.json()['thread_id'][:8]}:{text[:20]}"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模拟流量生成器(spec #9)")
    parser.add_argument("--base-url", default="http://localhost:3000", help="后端地址")
    parser.add_argument("--once", action="store_true", help="执行一轮后退出(冒烟)")
    parser.add_argument("--loop", type=int, metavar="SECONDS", help="每 N 秒一轮,直到 Ctrl+C")
    parser.add_argument("--daily-budget", type=int, default=200, help="每日 LLM 消耗动作上限(默认 200)")
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
    )
    try:
        return asyncio.run(_run(simulator, args))
    finally:
        asyncio.run(simulator.close())
