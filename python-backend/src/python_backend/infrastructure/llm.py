"""LLM 接入:httpx 直连 DeepSeek(OpenAI 兼容协议)。

宪章 ADR-0005 行为:
- 分层失败语义:瞬时错误(429/5xx/网络)本地重试 1-2 次,仍败上抛(永不静默吞错);4xx(非 429)不重试
- 并发闸:进程级 asyncio.Semaphore(单进程模型,settings.llm_max_concurrency=2)。
  闸为模块级单例——DeepSeek 限流是账号级,增量 4 起每 Agent 各建 LlmService 实例时闸仍全局生效
- Langfuse 埋点:host 留空则 no-op(不初始化);配置后记录 LLM 调用 trace/generation
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from python_backend.settings import get_settings

logger = logging.getLogger(__name__)

# 并发闸(宪章:LLM 并发闸 2 保留,DeepSeek 账号级限流防护)。
# 按事件循环缓存:生产单 loop 即进程级单例,增量 4 起每 Agent 各建 LlmService 实例时闸仍全局生效;
# asyncio.Semaphore 绑定其首次使用的 loop,不能做纯模块级单例(测试每用例新建 loop 会跨 loop 复用抛错)。
_LOOP_GATES: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _llm_gate() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    gate = _LOOP_GATES.get(loop)
    if gate is None:
        gate = _LOOP_GATES[loop] = asyncio.Semaphore(get_settings().llm_max_concurrency)
    return gate


class LlmFailure(Exception):
    """LLM 调用失败(重试耗尽/不可重试状态码/网络错误)。

    与编程错误(响应结构变化等)相区分:上层 fallback 只承接 LlmFailure,
    编程错误继续上抛(宪章:永不静默吞错)。
    """


@dataclass
class ToolCallResult:
    """工具调用轮次的结果:content 在纯作答时存在,tool_calls 为 OpenAI 原始格式。

    reasoning_content:思考模式模型的思维链(issue #27)。该字段须在下轮请求里原样回传
    (空串也要保留键),否则 provider 返 400;非思考模式响应无此键 → None(回传时省略)。
    """

    content: str | None
    tool_calls: list[dict]
    reasoning_content: str | None = None


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
        return ToolCallResult(
            content=message.get("content"),
            tool_calls=message.get("tool_calls") or [],
            reasoning_content=message.get("reasoning_content"),
        )

    async def _request(self, payload: dict) -> dict:
        url = get_settings().llm_api_url.rstrip("/") + "/v1/chat/completions"
        headers = {"Authorization": f"Bearer {get_settings().llm_api_key}"}
        model = get_settings().llm_model
        for delay in (*self._retry_delays, None):
            try:
                response = await self._client.post(url, json=payload, headers=headers)
                if response.status_code == 200:
                    message = response.json()["choices"][0]["message"]
                    self._tracer.record_generation("chat.completions", input=payload, output=message, model=model)
                    return message
                error: Exception = httpx.HTTPStatusError(
                    f"LLM 调用失败:HTTP {response.status_code}", request=response.request, response=response
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                error = exc  # 瞬时网络错误:落入下方统一的重试/上抛判定
            self._tracer.record_generation("chat.completions", input=payload, output={"error": str(error)}, model=model)
            if delay is None or not _is_retryable(error):
                raise LlmFailure(f"LLM 调用失败:{error}") from error
            logger.warning("LLM 调用失败(%s),%.1fs 后重试", type(error).__name__, delay)
            await _sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover - 循环结构保证不可达

    async def _with_gate(self, fn) -> dict:
        async with _llm_gate():
            return await fn()


def _default_tracer(settings) -> LlmTracer:
    """Langfuse 未配置 → NullTracer(no-op);配置后 → 自托管埋点。"""
    if settings.langfuse_host:
        return LangfuseTracer()
    return NullTracer()


def _is_retryable(error: Exception) -> bool:
    """瞬时错误判定:网络错误与 429/5xx 重试,4xx(非 429)不重试。"""
    if isinstance(error, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    response = getattr(error, "response", None)
    if response is None:
        return False
    return response.status_code == 429 or 500 <= response.status_code < 600


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
