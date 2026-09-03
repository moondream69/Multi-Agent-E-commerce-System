"""Embedding 接入:Ollama bge-m3(1024 维)。

与 NestJS 版的差异(按 ADR-0001):服务不可用或维度不符时**显式报错**,
不再静默降级为零向量(那会让搜索表现为"无结果且无报错")。
"""

from typing import Any

from langchain_ollama import OllamaEmbeddings
from sqlalchemy.orm import Session

from python_backend.db.search import semantic_search
from python_backend.settings import settings


class EmbeddingService:
    def __init__(self) -> None:
        self._client = OllamaEmbeddings(
            model=settings.embedding_model,
            base_url=settings.embedding_api_url,
        )

    def embed(self, text: str) -> list[float]:
        vector = self._client.embed_query(text)
        if len(vector) != settings.embedding_dimension:
            raise ValueError(
                f"Embedding 维度不符: 期望 {settings.embedding_dimension} 维, 实际 {len(vector)} 维"
            )
        return vector

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def search(
        self,
        session: Session,
        model: type,
        query: str,
        *,
        top_k: int = 5,
        threshold: float = 0.0,
        where: Any | None = None,
    ) -> list[dict]:
        vector = self.embed(query)
        return semantic_search(session, model, vector, top_k=top_k, threshold=threshold, where=where)
