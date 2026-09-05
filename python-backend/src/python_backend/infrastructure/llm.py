"""LLM 接入:langchain ChatOpenAI 指向 DeepSeek(OpenAI 兼容协议)。

可靠性加固(模拟流量长期运行的刚需):
- 全局并发闸:threading.Semaphore 限制进程内同时进行的 LLM 调用(DeepSeek 限流是账号级并发)
- 指数退避重试:429/5xx/网络错误重试 3 次(1s/2s/4s,尊重 Retry-After)
- 每服务熔断:连续 5 次失败 → 打开 60s,期间快速失败(不阻塞排队);60s 后半开探测

complete() 结果走 Redis 缓存,键/TTL 与 NestJS 版一致:
llm:{model}:{messages JSON}:{temperature},TTL 300s。
"""

import json
import logging
import threading
import time
from typing import Any

import openai
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from python_backend.domain.tasks import ToolDefinition
from python_backend.infrastructure.cache import redis_cache
from python_backend.settings import settings

logger = logging.getLogger(__name__)

_CACHE_TTL = 300
_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_MAX_TOKENS = 2000
_RETRY_DELAYS = (1.0, 2.0, 4.0)

_TYPE_MAP = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "object": "object",
    "array": "array",
}


def _openai_tools(tool_defs: list[ToolDefinition]) -> list[dict]:
    tools = []
    for d in tool_defs:
        parameters = {
            "type": "object",
            "properties": {p.name: {"type": _TYPE_MAP[p.type], "description": p.description} for p in d.parameters},
            "required": [p.name for p in d.parameters if p.required],
        }
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": d.name,
                    "description": d.description,
                    "parameters": parameters,
                },
            }
        )
    return tools


def _client(temperature: float, max_tokens: int) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_api_url.rstrip("/") + "/v1",
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=60.0,
    )


def _to_langchain(messages: list[dict]) -> list:
    """OpenAI 风格消息(role/content/tool_calls/tool_call_id)→ LangChain 消息。

    tool_calls 从 OpenAI 格式({id, type, function:{name, arguments}})转为
    LangChain 格式({name, args, id, type}),否则 AIMessage 构造会抛
    "tool_call() got an unexpected keyword argument 'function'"。
    """
    converted = []
    for msg in messages:
        role = msg["role"]
        content = msg.get("content") or ""
        if role == "system":
            converted.append(SystemMessage(content=content))
        elif role == "assistant":
            tool_calls = None
            if msg.get("tool_calls"):
                tool_calls = [
                    {
                        "name": tc["function"]["name"],
                        "args": json.loads(tc["function"]["arguments"] or "{}"),
                        "id": tc["id"],
                        "type": "function",
                    }
                    for tc in msg["tool_calls"]
                ]
            converted.append(AIMessage(content=content, tool_calls=tool_calls))
        elif role == "tool":
            converted.append(ToolMessage(content=content, tool_call_id=msg.get("tool_call_id")))
        else:
            converted.append(HumanMessage(content=content))
    return converted


def _retry_after_seconds(error: Exception) -> float | None:
    """429 响应头 Retry-After → 秒数;无则 None。"""
    headers = getattr(error, "response", None)
    headers = getattr(headers, "headers", None)
    if headers:
        value = headers.get("retry-after")
        if value:
            try:
                return float(value)
            except ValueError:
                return None
    return None


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, openai.RateLimitError):
        return True
    if isinstance(error, openai.APIStatusError):
        return 500 <= error.status_code < 600
    return isinstance(error, (TimeoutError, ConnectionError, OSError))


def _invoke_with_retry(fn) -> Any:
    """调用 fn,429/5xx/网络错误指数退避重试,4xx(非 429)不重试。"""
    last_error: Exception | None = None
    for delay in (*_RETRY_DELAYS, None):
        try:
            return fn()
        except Exception as error:
            last_error = error
            if delay is None or not _is_retryable(error):
                raise
            wait = _retry_after_seconds(error) or delay
            logger.warning("LLM 调用失败(%s),%ss 后重试: %s", type(error).__name__, wait, error)
            time.sleep(wait)
    raise last_error  # pragma: no cover - 循环结构保证不可达


class _CircuitBreaker:
    """按服务熔断:连续 threshold 次失败打开 open_seconds,期间快速失败;超时后半开。"""

    def __init__(self, threshold: int = 5, open_seconds: float = 60.0) -> None:
        self.threshold = threshold
        self.open_seconds = open_seconds
        self.failures = 0
        self.opened_at = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.opened_at:
                if time.monotonic() - self.opened_at >= self.open_seconds:
                    self.opened_at = 0.0
                    self.failures = 0
                    return True
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()
                logger.warning("LLM 熔断器打开(连续 %s 次失败),%ss 内快速失败", self.threshold, self.open_seconds)


class LlmService:
    def __init__(self) -> None:
        self._gate = threading.Semaphore(settings.llm_max_concurrency)
        self._breaker = _CircuitBreaker()

    def _guarded(self, fn) -> Any:
        if not self._breaker.allow():
            raise RuntimeError(f"LLM 熔断器已打开({settings.llm_model}),服务暂不可用,请稍后重试")
        with self._gate:
            try:
                result = _invoke_with_retry(fn)
                self._breaker.record_success()
                return result
            except Exception:
                self._breaker.record_failure()
                raise

    def complete(
        self,
        messages: list[dict],
        *,
        temperature: float = _DEFAULT_TEMPERATURE,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        json_mode: bool = False,
    ) -> str:
        cache_key = f"llm:{settings.llm_model}:{json.dumps(messages, ensure_ascii=False)}:{temperature}"
        cached = redis_cache.get_json(cache_key)
        if cached is not None:
            return cached["content"]

        response_format = {"type": "json_object"} if json_mode else None
        kwargs = {"response_format": response_format} if response_format else {}

        def _call() -> AIMessage:
            return _client(temperature, max_tokens).invoke(_to_langchain(messages), **kwargs)

        result = self._guarded(_call)

        content = result.content if isinstance(result.content, str) else json.dumps(result.content, ensure_ascii=False)
        redis_cache.set_json(cache_key, {"content": content}, ttl=_CACHE_TTL)
        return content

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[ToolDefinition],
        *,
        temperature: float = _DEFAULT_TEMPERATURE,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> AIMessage:
        """返回原始 AIMessage(含 tool_calls),由 ReAct 循环(状态图)消费。"""

        def _call() -> AIMessage:
            client = _client(temperature, max_tokens).bind_tools(_openai_tools(tools), tool_choice="auto")
            return client.invoke(_to_langchain(messages))

        return self._guarded(_call)
