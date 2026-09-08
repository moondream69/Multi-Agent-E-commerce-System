"""VectorRepository 契约测试(InMemory 参考实现):验证契约套件与接口语义,无需 Milvus 在线。"""

from __future__ import annotations

import math

import pytest

from python_backend.vector_repo.base import SearchHit, VectorRecord, VectorRepository
from tests.vector_repo_contracts import (
    contract_delete_removes_records,
    contract_search_filter_narrows_results,
    contract_search_respects_top_k,
    contract_upsert_same_id_overwrites,
    contract_upsert_then_search_returns_hits_sorted_by_score,
)


class InMemoryVectorRepository(VectorRepository):
    """契约参考实现:余弦相似度,验证套件逻辑与接口语义,不涉及 Milvus。"""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, VectorRecord]] = {}

    async def upsert(self, collection: str, records: list[VectorRecord]) -> None:
        self._store.setdefault(collection, {})
        for record in records:
            self._store[collection][record.id] = record

    async def search(
        self, collection: str, vector: list[float], *, top_k: int, filter: str | None = None
    ) -> list[SearchHit]:
        hits = []
        for record in self._store.get(collection, {}).values():
            if filter and not _matches(record, filter):
                continue
            hits.append(SearchHit(id=record.id, score=_cosine(vector, record.vector), payload=record.payload))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]

    async def delete(self, collection: str, ids: list[str]) -> None:
        for id_ in ids:
            self._store.get(collection, {}).pop(id_, None)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def _matches(record: VectorRecord, filter: str) -> bool:
    # 支持最简表达式:field == "value"(契约测试仅覆盖此形状)
    field, value = filter.split(" == ")
    return record.payload.get(field) == value.strip('"')


@pytest.fixture
def repo() -> VectorRepository:
    return InMemoryVectorRepository()


test_upsert_then_search_returns_hits_sorted_by_score = contract_upsert_then_search_returns_hits_sorted_by_score
test_search_respects_top_k = contract_search_respects_top_k
test_search_filter_narrows_results = contract_search_filter_narrows_results
test_upsert_same_id_overwrites = contract_upsert_same_id_overwrites
test_delete_removes_records = contract_delete_removes_records
