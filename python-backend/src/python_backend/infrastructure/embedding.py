"""Embedding 接入:httpx 直连 Ollama(`POST /api/embed`),模型 bge-m3(1024 维)。

宪章 ADR-0005 行为:
- 返回向量须与 settings.embedding_dimension 一致,不符显式报错(模型配置错误不静默接受)
- 服务不可用(网络/HTTP 错误)显式上抛——绝不静默降级为零向量
"""

from __future__ import annotations

from typing import Protocol

import httpx

from python_backend.settings import get_settings


class EmbeddingClient(Protocol):
    """向量化协议:executor/drafting 共享的注入接缝(测试假实现)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingService:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(transport=transport, timeout=60.0)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        settings = get_settings()
        response = await self._client.post(
            settings.embedding_api_url.rstrip("/") + "/api/embed",
            json={"model": settings.embedding_model, "input": texts},
        )
        response.raise_for_status()
        vectors = response.json()["embeddings"]
        if len(vectors) != len(texts):
            raise ValueError(f"Embedding 返回条数不符:期望 {len(texts)},实际 {len(vectors)}")
        expected = settings.embedding_dimension
        if any(len(v) != expected for v in vectors):
            actual = {len(v) for v in vectors}
            raise ValueError(f"Embedding 维度不符:期望 {expected},实际 {actual}(检查 EMBEDDING_MODEL 配置)")
        return vectors
