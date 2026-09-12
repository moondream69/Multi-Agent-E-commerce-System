"""汇率服务测试(spec #8 B10):快照计算 + 缓存降级链(API 失效 → 缓存 → 如实报错)。

不依赖真实网络:假 FxClient 脚本化三态(成功/失效/币种缺失),假 Redis 内存断言缓存读写;
ErApiFxClient 的响应解析用 httpx.MockTransport 离线验证。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
import pytest

from python_backend.infrastructure.fx import (
    ErApiFxClient,
    FxService,
    FxUnavailableError,
)
from python_backend.settings import Settings


class FakeFxClient:
    """脚本化汇率客户端:latest_calls 计数,scripted 字典控制行为。"""

    def __init__(self, scripted: dict[str, Decimal] | None = None, *, fail: bool = False) -> None:
        self.scripted = scripted or {}
        self.fail = fail
        self.latest_calls = 0

    async def latest(self, base: str) -> dict[str, Decimal]:
        self.latest_calls += 1
        if self.fail:
            raise httpx.ConnectError("汇率 API 不可达")
        return {**self.scripted, base: Decimal(1)}


class FakeRedis:
    """内存假 Redis(仅 get/setex):按 FxService 使用的接口裁剪。"""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = value.encode()


async def test_cny_identity_without_network() -> None:
    """基准币自身恒为 1:不走客户端也不走缓存。"""
    client = FakeFxClient()
    service = FxService(client=client, redis=FakeRedis())
    assert await service.get_rate_cny("cny") == Decimal(1)
    assert client.latest_calls == 0


async def test_api_success_computes_and_caches_rate() -> None:
    """API 成功:1 单位币种兑 CNY = 1 / rates[币种],8 位小数,写缓存。"""
    client = FakeFxClient({"USD": Decimal("0.139")})
    redis = FakeRedis()
    service = FxService(client=client, redis=redis)
    rate = await service.get_rate_cny("USD")
    assert rate == (Decimal(1) / Decimal("0.139")).quantize(Decimal("0.00000001"))
    assert await redis.get("fx:rate:USD") == str(rate).encode()


async def test_cache_hit_skips_api() -> None:
    """缓存命中直接返回:API 不被调用。"""
    client = FakeFxClient({"USD": Decimal("0.139")})
    redis = FakeRedis()
    await redis.setex("fx:rate:USD", 3600, "7.19424460")
    service = FxService(client=client, redis=redis)
    assert await service.get_rate_cny("USD") == Decimal("7.19424460")
    assert client.latest_calls == 0


async def test_api_fail_with_cache_falls_back() -> None:
    """API 失效 + 有缓存:降级返回缓存值(降级链)。"""
    client = FakeFxClient(fail=True)
    redis = FakeRedis()
    await redis.setex("fx:rate:USD", 3600, "7.10000000")
    service = FxService(client=client, redis=redis)
    assert await service.get_rate_cny("USD") == Decimal("7.10000000")


async def test_api_fail_without_cache_raises() -> None:
    """API 失效且缓存为空:抛 FxUnavailableError(下单方如实报错,不落无快照订单)。"""
    service = FxService(client=FakeFxClient(fail=True), redis=FakeRedis())
    with pytest.raises(FxUnavailableError):
        await service.get_rate_cny("USD")


async def test_missing_currency_raises() -> None:
    """API 未返回目标币种:抛错而非猜测汇率。"""
    service = FxService(client=FakeFxClient({"EUR": Decimal("0.12")}), redis=FakeRedis())
    with pytest.raises(FxUnavailableError):
        await service.get_rate_cny("USD")


def test_erapi_client_parses_rates_offline() -> None:
    """ErApiFxClient 解析:MockTransport 离线验证 {rates} → Decimal 字典;基准币注入 1。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/CNY")
        return httpx.Response(200, json={"result": "success", "rates": {"USD": 0.139, "EUR": "0.12", "BAD": "x"}})

    async def run() -> dict[str, Decimal]:
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = ErApiFxClient(api_url="https://example.invalid/v6/latest", http_client=http)
        return await client.latest("CNY")

    rates = asyncio.run(run())
    assert rates == {"USD": Decimal("0.139"), "EUR": Decimal("0.12"), "CNY": Decimal(1)}


async def test_default_api_url_composes_to_base_endpoint() -> None:
    """issue #29:默认 fx_api_url + 客户端追加 /{base} 须拼出 .../v6/latest/CNY。
    配置里若含基准币,会拼成 .../latest/CNY/CNY → 404,汇率快照恒失效。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"result": "success", "rates": {"USD": 0.139}})

    default = Settings.model_fields["fx_api_url"].default
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ErApiFxClient(api_url=default, http_client=http)
    rates = await client.latest("CNY")

    assert seen == ["/v6/latest/CNY"]
    assert rates == {"USD": Decimal("0.139"), "CNY": Decimal(1)}


async def test_erapi_client_rejects_missing_rates() -> None:
    """响应缺 rates 字段:抛 ValueError(由 FxService 包装为 FxUnavailableError)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": "error", "error-type": "unknown-code"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ErApiFxClient(api_url="https://example.invalid/v6/latest", http_client=http)
    with pytest.raises(ValueError):
        await client.latest("CNY")
