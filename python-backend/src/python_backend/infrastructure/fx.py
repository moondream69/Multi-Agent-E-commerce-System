"""汇率服务(spec #8 B10):实时汇率快照(基准 CNY)+ Redis 缓存降级。

- FxClient 协议:拉取基准币兑各币种汇率(测试注入假实现)
- ErApiFxClient:settings.fx_api_url(er-api v6 免 key,响应 {"rates": {...}})
- FxService.get_rate_cny(currency):1 单位币种兑 CNY 的汇率,8 位小数(Numeric(18,8) 对齐);
  缓存优先 → API 拉取 → 两者皆无抛 FxUnavailableError(下单方如实报错,不落无快照订单)
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

from python_backend.settings import get_settings

logger = logging.getLogger(__name__)

CNY = "CNY"
FX_CACHE_TTL_SECONDS = 4 * 60 * 60  # 短时状态(宪章:Redis 定位);er-api 数据日更,4h 合理
FX_CACHE_KEY_PREFIX = "fx:rate:"
FX_DECIMALS = Decimal("0.00000001")  # 8 位小数,与 orders.fx_rate Numeric(18,8) 对齐


class FxUnavailableError(Exception):
    """汇率 API 失效且缓存为空(或币种缺失):下单方须如实报错,不落无快照订单(B10 暂态)。"""


class FxClient(Protocol):
    """汇率客户端协议:返回 {币种: 1 基准币兑该币种的数量}(基准币自身为 1)。"""

    async def latest(self, base: str) -> dict[str, Decimal]: ...


class FxCache(Protocol):
    """汇率缓存协议(FxService 使用的 redis 子集):测试注入内存假实现。"""

    async def get(self, key: str) -> bytes | str | None: ...

    async def setex(self, key: str, ttl: int, value: str) -> None: ...


class ErApiFxClient:
    """er-api v6 客户端(免 key):GET {fx_api_url}/{base} → {"rates": {"USD": 0.139, ...}}。

    非法响应(缺 rates / 非 dict)抛 ValueError,由 FxService 统一包装为 FxUnavailableError。
    """

    def __init__(self, api_url: str | None = None, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._api_url = (api_url or get_settings().fx_api_url).rstrip("/")
        self._http_client = http_client  # 测试注入(MockTransport 离线验证),生产 None 自建

    async def latest(self, base: str) -> dict[str, Decimal]:
        if self._http_client is not None:
            response = await self._http_client.get(f"{self._api_url}/{base}")
            response.raise_for_status()
        else:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self._api_url}/{base}")
                response.raise_for_status()
        payload = response.json()
        rates = payload.get("rates")
        if not isinstance(rates, dict):
            raise ValueError(f"汇率 API 响应缺 rates:{str(payload)[:200]}")
        result: dict[str, Decimal] = {}
        for code, value in rates.items():
            try:
                result[code] = Decimal(str(value))
            except InvalidOperation:
                continue  # 单币种解析失败不拖垮整表(其余币种仍可用)
        result[base] = Decimal(1)
        return result


class FxService:
    """汇率服务:缓存优先 → API 拉取并写缓存;API 失效时缓存已在第一步检查过(降级语义)。

    redis 可注入(测试用内存假实现);None 时跳过缓存(仅 API,单测降级链路注入假缓存)。
    """

    def __init__(self, *, client: FxClient | None = None, redis: FxCache | None = None) -> None:
        self._client = client or ErApiFxClient()
        self._redis = redis

    def _cache_key(self, currency: str) -> str:
        return f"{FX_CACHE_KEY_PREFIX}{currency}"

    async def _read_cache(self, currency: str) -> Decimal | None:
        if self._redis is None:
            return None
        raw = await self._redis.get(self._cache_key(currency))
        if raw is None:
            return None
        try:
            return Decimal(raw if isinstance(raw, str) else raw.decode())
        except InvalidOperation:
            return None  # 脏缓存视为未命中

    async def _write_cache(self, currency: str, rate: Decimal) -> None:
        if self._redis is not None:
            await self._redis.setex(self._cache_key(currency), FX_CACHE_TTL_SECONDS, str(rate))

    async def get_rate_cny(self, currency: str) -> Decimal:
        """1 单位 currency 兑 CNY 的汇率(基准 CNY)。CNY 自身恒为 1,不走网络。"""
        if currency.upper() == CNY:
            return Decimal(1)
        cached = await self._read_cache(currency)
        if cached is not None:
            return cached
        try:
            rates = await self._client.latest(CNY)
        except Exception as error:  # 网络/HTTP/解析错误 → 如实上报(缓存已查过为空)
            logger.warning("汇率 API 失败且缓存为空: %s", error)
            raise FxUnavailableError(f"汇率 API 失效且无缓存(币种 {currency})") from error
        per_cny = rates.get(currency)
        if per_cny is None or per_cny == 0:
            raise FxUnavailableError(f"汇率 API 未返回币种 {currency} 的有效汇率")
        rate = (Decimal(1) / per_cny).quantize(FX_DECIMALS)
        await self._write_cache(currency, rate)
        return rate
