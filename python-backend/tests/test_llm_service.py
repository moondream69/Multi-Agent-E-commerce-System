"""LlmService 契约测试(httpx MockTransport,无需真实 LLM 在线)。

验证 seam:complete/complete_with_tools 正常返回、分层失败语义(瞬时错误重试 1-2 次、
仍败上抛不静默、4xx 不重试)、并发闸、Langfuse 埋点开关。
"""

import httpx
import pytest

from python_backend.infrastructure.llm import LlmFailure, LlmService


def mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def json_ok(content: str, status: int = 200) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    return mock_transport(handler)


async def test_complete_returns_content() -> None:
    svc = LlmService(transport=json_ok("你好"))
    result = await svc.complete([{"role": "user", "content": "hi"}])
    assert result == "你好"


def counting_transport(responses: list[httpx.Response]) -> tuple[httpx.MockTransport, list[int]]:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(len(calls))
        return responses[min(len(calls) - 1, len(responses) - 1)]

    return mock_transport(handler), calls


async def test_complete_retries_on_transient_5xx_then_succeeds() -> None:
    transport, calls = counting_transport(
        [
            httpx.Response(500, json={"error": "boom"}),
            httpx.Response(200, json={"choices": [{"message": {"content": "恢复"}}]}),
        ]
    )
    svc = LlmService(transport=transport, retry_delays=(0, 0))
    result = await svc.complete([{"role": "user", "content": "hi"}])
    assert result == "恢复"
    assert len(calls) == 2


async def test_complete_raises_after_retries_exhausted() -> None:
    transport, calls = counting_transport([httpx.Response(500, json={"error": "boom"})])
    svc = LlmService(transport=transport, retry_delays=(0, 0))
    with pytest.raises(LlmFailure):
        await svc.complete([{"role": "user", "content": "hi"}])
    assert len(calls) == 3  # 1 次 + 2 次重试(分层失败语义:本地重试 1-2 次,仍败上抛)


async def test_complete_does_not_retry_on_4xx() -> None:
    transport, calls = counting_transport([httpx.Response(400, json={"error": "bad request"})])
    svc = LlmService(transport=transport, retry_delays=(0, 0))
    with pytest.raises(LlmFailure):
        await svc.complete([{"role": "user", "content": "hi"}])
    assert len(calls) == 1


async def test_complete_json_mode_sends_response_format() -> None:
    sent: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    svc = LlmService(transport=mock_transport(handler))
    await svc.complete([{"role": "user", "content": "hi"}], json_mode=True)
    assert sent is not None
    assert '"response_format"' in sent
    assert '"json_object"' in sent


async def test_concurrency_gate_limits_active_requests() -> None:
    import asyncio

    active = 0
    peak = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    svc = LlmService(transport=httpx.MockTransport(handler), retry_delays=(0, 0))
    results = await asyncio.gather(*[svc.complete([{"role": "user", "content": "hi"}]) for _ in range(6)])
    assert all(r == "ok" for r in results)
    assert peak <= 2  # settings.llm_max_concurrency=2(DeepSeek 账号级限流防护)


async def test_complete_with_tools_returns_tool_calls() -> None:
    message = {
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "faq_search", "arguments": '{"query": "物流时效"}'},
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        assert '"tools"' in body  # 工具以 OpenAI function 格式透传
        assert '"faq_search"' in body
        return httpx.Response(200, json={"choices": [{"message": message}]})

    svc = LlmService(transport=mock_transport(handler))
    result = await svc.complete_with_tools(
        [{"role": "user", "content": "帮我查物流"}],
        tools=[{"type": "function", "function": {"name": "faq_search", "parameters": {}}}],
    )
    assert result.content is None
    assert result.tool_calls[0]["function"]["name"] == "faq_search"


async def test_complete_with_tools_preserves_empty_reasoning_content() -> None:
    """issue #27 思考模式:响应带 reasoning_content → 原样透传(空串保留该键,不当作缺失)。"""
    message = {"content": None, "reasoning_content": "", "tool_calls": []}
    svc = LlmService(transport=mock_transport(lambda _r: httpx.Response(200, json={"choices": [{"message": message}]})))
    result = await svc.complete_with_tools([{"role": "user", "content": "hi"}], tools=[])
    assert result.reasoning_content == ""


async def test_complete_with_tools_reasoning_content_absent_is_none() -> None:
    """非思考模式响应无该键 → None(回传时须省略,不得凭空造字段)。"""
    message = {"content": "答案", "tool_calls": []}
    svc = LlmService(transport=mock_transport(lambda _r: httpx.Response(200, json={"choices": [{"message": message}]})))
    result = await svc.complete_with_tools([{"role": "user", "content": "hi"}], tools=[])
    assert result.reasoning_content is None


class FakeTracer:
    """埋点探针:tracer 协议(record_generation)的实现记录,验证 Langfuse 埋点开关与调用。"""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def record_generation(self, name: str, *, input: dict, output: dict, model: str) -> None:
        self.records.append({"name": name, "input": input, "output": output, "model": model})


async def test_tracer_records_successful_generation() -> None:
    tracer = FakeTracer()
    svc = LlmService(transport=json_ok("你好"), tracer=tracer)
    await svc.complete([{"role": "user", "content": "hi"}])
    assert len(tracer.records) == 1
    record = tracer.records[0]
    assert record["name"] == "chat.completions"
    assert record["input"]["messages"] == [{"role": "user", "content": "hi"}]
    assert record["output"]["content"] == "你好"
    assert record["model"] == "deepseek-v4-flash"


async def test_tracer_records_failures_before_raising() -> None:
    """分层失败语义:上抛前最后一次失败也要进观测(永不静默吞错,含观测侧)。"""
    tracer = FakeTracer()
    transport = counting_transport([httpx.Response(500, json={"error": "boom"})])[0]
    svc = LlmService(transport=transport, tracer=tracer, retry_delays=(0, 0))
    with pytest.raises(LlmFailure):
        await svc.complete([{"role": "user", "content": "hi"}])
    assert len(tracer.records) == 3  # 每次尝试(含重试)均记录
    assert all("error" in r["output"] for r in tracer.records)


async def test_concurrency_gate_is_shared_across_instances() -> None:
    """宪章:并发闸是账号级防护——增量 4 起每 Agent 各建 LlmService,闸必须跨实例共享。"""
    import asyncio

    active = 0
    peak = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    svc_a = LlmService(transport=httpx.MockTransport(handler), retry_delays=(0, 0))
    svc_b = LlmService(transport=httpx.MockTransport(handler), retry_delays=(0, 0))
    results = await asyncio.gather(
        *[svc_a.complete([{"role": "user", "content": "hi"}]) for _ in range(3)],
        *[svc_b.complete([{"role": "user", "content": "hi"}]) for _ in range(3)],
    )
    assert all(r == "ok" for r in results)
    assert peak <= 2


async def test_noop_when_langfuse_unconfigured() -> None:
    """settings.langfuse_host 留空 → 默认 NullTracer,不初始化 SDK、不抛错。"""
    svc = LlmService(transport=json_ok("ok"))
    assert await svc.complete([{"role": "user", "content": "hi"}]) == "ok"
