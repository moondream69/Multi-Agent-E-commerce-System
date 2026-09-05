"""模拟流量生成器:持续生成逼真的买家行为,驱动系统在真实感数据流上长期运行。

用法(需后端已启动并完成 seed):
    uv run python -m python_backend.simulator --once                 # 冒烟:执行一轮
    uv run python -m python_backend.simulator --loop 300             # 每 300 秒一轮,直到 Ctrl+C
    uv run python -m python_backend.simulator --loop 300 --include-ops
    uv run python -m python_backend.simulator --loop 300 --daily-budget 200

全部走 HTTP 真入口(先登录拿 JWT,再调 store/agent API),不直写业务表;
业务写入由系统自身完成(agent_tasks 审计、conversations、approval_requests)。
LLM 预算:下单/咨询/投诉/运营指令都触发 LLM 调用,达预算后当天剩余轮次只做浏览。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
from datetime import UTC, datetime

import httpx

from python_backend.simulator.scenarios import (
    BUYER_EMAILS,
    COMPLAINTS,
    FOLLOW_UP_QUESTIONS,
    OPS_COMMANDS,
    SERVICE_QUESTIONS,
)

logger = logging.getLogger("simulator")

# 行为权重:(名称, 权重)。浏览不计 LLM 预算。
_ACTION_WEIGHTS = [("browse", 5), ("order", 40), ("service", 35), ("complain", 10)]


class Simulator:
    def __init__(self, base_url: str, username: str, password: str, daily_budget: int = 200) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.daily_budget = daily_budget
        self.budget_date = datetime.now(UTC).date()
        self.budget_spent = 0
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    async def close(self) -> None:
        await self.client.aclose()

    async def login(self) -> None:
        res = await self.client.post(
            "/api/auth/login",
            json={"username": self.username, "password": self.password},
        )
        if res.status_code != 200:
            raise RuntimeError(f"登录失败({res.status_code}): {res.text[:200]}")
        token = res.json()["token"]
        self.client.headers["Authorization"] = f"Bearer {token}"

    def _budget_left(self) -> int:
        today = datetime.now(UTC).date()
        if today != self.budget_date:
            self.budget_date = today
            self.budget_spent = 0
            logger.info("新的一天,预算重置为 %s", self.daily_budget)
        return self.daily_budget - self.budget_spent

    async def browse(self) -> None:
        res = await self.client.get("/api/products")
        if res.status_code != 200:
            logger.warning("浏览失败: %s", res.status_code)

    async def place_order(self) -> None:
        res = await self.client.get("/api/products")
        if res.status_code != 200:
            logger.warning("取商品列表失败: %s", res.status_code)
            return
        products = [p for p in res.json() if p["status"] == "active"]
        if not products:
            logger.warning("无 active 商品,跳过下单")
            return
        product = random.choice(products)
        email = random.choice(BUYER_EMAILS)
        res = await self.client.post(
            "/api/orders",
            json={"productId": product["id"], "customerEmail": email},
        )
        if res.status_code != 200:
            logger.warning("下单失败(%s): %s", res.status_code, res.text[:150])
            return
        order = res.json()
        self.budget_spent += 1  # 下单触发客服通知链(烧 LLM)
        logger.info("下单成功: %s 买 %s (订单 %s)", email, product["title"], order["id"])
        # 30-90s 后买家催单,把订单事件链 + 客服通知跑活
        question = random.choice(FOLLOW_UP_QUESTIONS).format(order_id=order["id"][:8])
        await self.ask_service(question)

    async def ask_service(self, text: str | None = None) -> None:
        if self._budget_left() <= 0:
            return
        if text is None:
            text = random.choice(SERVICE_QUESTIONS)
            if "#{order_id}" in text:
                text = text.replace("#{order_id}", f"#{random.randint(1000, 9999)}")
        res = await self.client.post(
            "/api/agents/task",
            json={"type": "customer_service", "input": {"originalText": text, "text": text}},
        )
        self.budget_spent += 1
        if res.status_code != 200:
            logger.warning("咨询失败(%s): %s", res.status_code, res.text[:150])
            return
        result = res.json()
        status = result.get("status")
        if status == "failed":
            logger.warning("咨询任务失败: %s", result.get("output", {}).get("error", "")[:150])
        else:
            logger.info("咨询完成(%s): %s", status, text[:40])

    async def complain(self) -> None:
        if self._budget_left() <= 0:
            return
        text = random.choice(COMPLAINTS)
        res = await self.client.post(
            "/api/agents/task",
            json={"type": "customer_service", "input": {"originalText": text, "text": text}},
        )
        self.budget_spent += 1
        if res.status_code != 200:
            logger.warning("投诉处理失败(%s): %s", res.status_code, res.text[:150])
            return
        result = res.json()
        logger.info("投诉处理完成(%s): %s", result.get("status"), text[:30])

    async def ops_command(self) -> None:
        if self._budget_left() <= 0:
            return
        text = random.choice(OPS_COMMANDS)
        res = await self.client.post(
            "/api/agents/task",
            json={"type": "order_management", "input": {"originalText": text, "text": text}},
        )
        self.budget_spent += 1
        if res.status_code != 200:
            logger.warning("运营指令失败(%s): %s", res.status_code, res.text[:150])

    async def run_once(self, include_ops: bool = False) -> None:
        """执行一轮随机买家行为(1-3 个动作)。"""
        actions = [
            random.choices([a for a, _ in _ACTION_WEIGHTS], weights=[w for _, w in _ACTION_WEIGHTS])[0]
            for _ in range(random.randint(1, 3))
        ]
        for action in actions:
            if action == "browse":
                await self.browse()
            elif action == "order":
                await self.place_order()
            elif action == "service":
                await self.ask_service()
            elif action == "complain":
                await self.complain()
        if include_ops and self._budget_left() > 0 and random.random() < 0.1:
            await self.ops_command()
        logger.info("本轮完成,预算剩余 %s/%s", self._budget_left(), self.daily_budget)


async def _run(
    base_url: str, username: str, password: str, loop_seconds: int | None, daily_budget: int, include_ops: bool
) -> None:
    sim = Simulator(base_url, username, password, daily_budget)
    try:
        await sim.login()
        logger.info("已登录 %s,开始模拟流量", username)
        while True:
            await sim.run_once(include_ops)
            if loop_seconds is None:
                break
            await asyncio.sleep(loop_seconds)
    finally:
        await sim.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="模拟流量生成器")
    parser.add_argument("--base-url", default="http://127.0.0.1:3000", help="后端地址")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="")
    parser.add_argument("--loop", type=float, default=None, help="每 N 秒一轮;缺省仅跑一轮")
    parser.add_argument("--daily-budget", type=int, default=200, help="每天 LLM 任务预算(达到后只浏览)")
    parser.add_argument("--include-ops", action="store_true", help="包含运营指令(选品分析,烧 token 较多)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    if not args.password:
        logging.getLogger("simulator").warning("未提供 --password,请使用后端 seed 生成的 admin 密码")

    asyncio.run(_run(args.base_url, args.username, args.password, args.loop, args.daily_budget, args.include_ops))


if __name__ == "__main__":
    main()
