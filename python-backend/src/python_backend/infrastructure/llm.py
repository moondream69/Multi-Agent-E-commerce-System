"""LLM 接入:httpx 直连 DeepSeek(OpenAI 兼容协议)。

宪章 ADR-0005 行为:
- 分层失败语义:瞬时错误(429/5xx/网络)本地重试 1-2 次,仍败上抛(永不静默吞错);4xx(非 429)不重试
- 并发闸:进程内 asyncio.Semaphore(单进程模型,settings.llm_max_concurrency=2)
- Langfuse 埋点:host 留空则 no-op(不初始化);配置后记录 LLM 调用 trace/generation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from python_backend.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass
class ToolCallResult:
    """工具调用轮次的结果:content 在纯作答时存在,tool_calls 为 OpenAI 原始格式。"""

    content: str | None
    tool_calls: list[dict]


class LlmTracer(Protocol):
    """LLM 调用埋点协议:Langfuse 记录每次 HTTP 尝试(含重试与失败),实现可替换。"""

    def record_generation(self, name: str, *, input: dict, output: dict, model: str) -> None: ...


class LlmClient(Protocol):
    """LLM 客户端协议:规划器等调用方依赖此协议而非具体实现(测试注入 FakeLlm)。"""

    async def complete(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        json_mode: bool = False,
    ) -> str: ...


class NullTracer:
    """Langfuse 未配置(host 留空)时的 no-op 埋点:不初始化 SDK、不抛错。"""

    def record_generation(self, name: str, *, input: dict, output: dict, model: str) -> None:
        return None


class LangfuseTracer:
    """Langfuse 自托管埋点:每次 LLM HTTP 尝试记录一个 generation(观测重试轨迹)。"""

    def __init__(self) -> None:
        settings = get_settings()
        self._enabled = bool(settings.langfuse_host and settings.langfuse_public_key and settings.langfuse_secret_key)
        self._client: Any = None
        if self._enabled:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )

    def record_generation(self, name: str, *, input: dict, output: dict, model: str) -> None:
        if not self._enabled:
            return
        observation = self._client.start_observation(
            name=name, as_type="generation", input=input, output=output, model=model
        )
        observation.end()


class LlmService:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        tracer: LlmTracer | None = None,
        retry_delays: tuple[float, float] = (1.0, 2.0),
    ) -> None:
        settings = get_settings()
        self._client = httpx.AsyncClient(transport=transport, timeout=60.0)
        self._tracer = tracer if tracer is not None else _default_tracer(settings)
        self._retry_delays = retry_delays
        self._gate = _concurrency_gate(settings.llm_max_concurrency)

    async def complete(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        json_mode: bool = False,
    ) -> str:
        """纯文本补全。json_mode=True 时请求 JSON 输出(规划用)。"""
        payload: dict[str, Any] = {
            "model": get_settings().llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        message = await self._with_gate(lambda: self._request(payload))
        return message["content"] or ""

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> ToolCallResult:
        """工具调用轮次:tools 以 OpenAI function 格式透传(动态暴露由图侧负责组装)。"""
        payload: dict[str, Any] = {
            "model": get_settings().llm_model,
            "messages": messages,
            "tools": tools,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        message = await self._with_gate(lambda: self._request(payload))
        return ToolCallResult(content=message.get("content"), tool_calls=message.get("tool_calls") or [])

    async def _request(self, payload: dict) -> dict:
        url = get_settings().llm_api_url.rstrip("/") + "/v1/chat/completions"
        headers = {"Authorization": f"Bearer {get_settings().llm_api_key}"}
        model = get_settings().llm_model
        last_error: Exception | None = None
        for delay in (*self._retry_delays, None):
            try:
                response = await self._client.post(url, json=payload, headers=headers)
                if response.status_code == 200:
                    message = response.json()["choices"][0]["message"]
                    self._tracer.record_generation("chat.completions", input=payload, output=message, model=model)
                    return message
                error = httpx.HTTPStatusError(
                    f"LLM 调用失败:HTTP {response.status_code}", request=response.request, response=response
                )
                self._tracer.record_generation(
                    "chat.completions", input=payload, output={"error": str(error)}, model=model
                )
                if not _is_retryable(error) or delay is None:
                    raise error
            except (httpx.ConnectError, httpx.TimeoutException) as error:
                self._tracer.record_generation(
                    "chat.completions", input=payload, output={"error": str(error)}, model=model
                )
                if delay is None:
                    raise
                error = error
            last_error = error
            logger.warning("LLM 调用失败(%s),%.1fs 后重试", type(error).__name__, delay)
            await _sleep(delay)
        raise last_error  # pragma: no cover - 循环结构保证不可达

    async def _with_gate(self, fn) -> dict:
        async with self._gate:
            return await fn()


def _default_tracer(settings) -> LlmTracer:
    """Langfuse 未配置 → NullTracer(no-op);配置后 → 自托管埋点。"""
    if settings.langfuse_host:
        return LangfuseTracer()
    return NullTracer()


def _concurrency_gate(limit: int):
    import asyncio

    return asyncio.Semaphore(limit)


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, httpx.TimeoutException):
        return True
    response = getattr(error, "response", None)
    if response is None:
        return False
    return response.status_code == 429 or 500 <= response.status_code < 600


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
