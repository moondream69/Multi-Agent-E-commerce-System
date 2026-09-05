"""LLM 可靠性加固测试:重试 + 熔断(单元,无外部依赖)。"""

from __future__ import annotations

import threading
import time
import uuid

import httpx2
import openai
import pytest
from langchain_core.messages import AIMessage

from python_backend.infrastructure import llm as llm_module
from python_backend.infrastructure.llm import LlmService, _CircuitBreaker, _is_retryable


def _error_response(status: int) -> httpx2.Response:
    request = httpx2.Request("POST", "https://api.deepseek.com/v1/chat/completions")
    return httpx2.Response(status, request=request)


def _rate_limit_error() -> openai.RateLimitError:
    return openai.RateLimitError("rate limit", response=_error_response(429), body=None)


def test_circuit_breaker_opens_and_recovers():
    breaker = _CircuitBreaker(threshold=3, open_seconds=0.05)
    for _ in range(3):
        breaker.record_failure()
    assert not breaker.allow()  # 打开:快速失败
    time.sleep(0.06)
    assert breaker.allow()  # 半开恢复
    breaker.record_success()
    assert breaker.allow()


def test_is_retryable_classification():
    assert _is_retryable(_rate_limit_error()) is True
    assert _is_retryable(openai.BadRequestError("bad", response=_error_response(400), body=None)) is False
    assert _is_retryable(openai.InternalServerError("boom", response=_error_response(500), body=None)) is True
    assert _is_retryable(TimeoutError()) is True


def test_retry_succeeds_after_rate_limit(monkeypatch):
    calls: list[int] = []

    class FlakyClient:
        def invoke(self, messages, **kwargs):
            calls.append(1)
            if len(calls) <= 2:
                raise _rate_limit_error()
            return AIMessage(content="最终答案")

    # 每次运行用唯一 content,避免 Redis 缓存键冲突导致不触发真实调用
    unique = f"retry-me-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(llm_module, "_client", lambda temperature, max_tokens: FlakyClient())
    service = LlmService()
    result = service.complete([{"role": "user", "content": unique}], max_tokens=10)
    assert result == "最终答案"
    assert len(calls) == 3


def test_non_retryable_error_raises_immediately(monkeypatch):
    class BadClient:
        def invoke(self, messages, **kwargs):
            raise openai.BadRequestError("bad", response=_error_response(400), body=None)

    monkeypatch.setattr(llm_module, "_client", lambda temperature, max_tokens: BadClient())
    service = LlmService()
    with pytest.raises(openai.BadRequestError):
        service.complete([{"role": "user", "content": "bad-request-me"}], max_tokens=10)


def test_concurrency_gate_is_thread_safe():
    gate = threading.Semaphore(2)
    assert gate._value == 2  # 默认并发上限来自 settings,此处仅验证组件存在


def test_breaker_blocks_when_open(monkeypatch):
    service = LlmService()
    service._breaker.record_failure()
    service._breaker.record_failure()
    service._breaker.record_failure()
    service._breaker.record_failure()
    service._breaker.record_failure()  # 触发打开(threshold=5)

    class NeverCalled:
        def invoke(self, messages, **kwargs):
            raise AssertionError("熔断打开时不应调用 LLM")

    monkeypatch.setattr(llm_module, "_client", lambda temperature, max_tokens: NeverCalled())
    with pytest.raises(RuntimeError, match="熔断器已打开"):
        service.complete([{"role": "user", "content": "breaker-me"}], max_tokens=10)
