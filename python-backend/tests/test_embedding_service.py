"""EmbeddingService 契约测试(httpx MockTransport,无需 Ollama 在线)。

验证 seam:embed 返回配置维度向量;维度不符/服务不可用显式报错(宪章:绝不静默降级为零向量)。
"""

import httpx
import pytest

from python_backend.infrastructure.embedding import EmbeddingService


def embeddings_ok(vectors: list[list[float]], status: int = 200) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"embeddings": vectors})

    return httpx.MockTransport(handler)


async def test_embed_returns_configured_dimension() -> None:
    svc = EmbeddingService(transport=embeddings_ok([[0.1] * 1024, [0.2] * 1024]))
    result = await svc.embed(["什么是物流时效", "如何退货"])
    assert len(result) == 2
    assert all(len(v) == 1024 for v in result)


async def test_embed_raises_on_dimension_mismatch() -> None:
    """维度不符 = 模型配置错误,显式报错而非静默接受。"""
    svc = EmbeddingService(transport=embeddings_ok([[0.1] * 512]))
    with pytest.raises(ValueError, match="维度"):
        await svc.embed(["hello"])


async def test_embed_raises_when_ollama_unavailable() -> None:
    """宪章:Embedding 服务不可用显式报错(不静默降级为零向量)。"""
    svc = EmbeddingService(transport=httpx.MockTransport(lambda _r: httpx.Response(503, json={"error": "down"})))
    with pytest.raises(httpx.HTTPStatusError):
        await svc.embed(["hello"])


async def test_embed_raises_on_connection_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    svc = EmbeddingService(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ConnectError):
        await svc.embed(["hello"])
