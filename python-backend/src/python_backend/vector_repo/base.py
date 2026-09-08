"""向量仓库接口(术语表:VectorRepository)。

宪章 ADR-0005:向量访问的统一抽象,当前实现 Milvus Standalone,pgvector 可切换退路。
score 语义统一为相似度(越大越近);filter 为表达式字符串(当前实现 Milvus 语法)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VectorRecord:
    """一条待写入的向量记录:payload 为业务标量(商品/FAQ/情报字段)。"""

    id: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchHit:
    """一次检索命中:score 为余弦相似度(大=近;取值范围 -1~1)。"""

    id: str
    score: float
    payload: dict[str, Any]


class VectorRepository(ABC):
    """向量访问抽象:所有实现(Milvus/pgvector/内存)必须满足同一契约。"""

    @abstractmethod
    async def upsert(self, collection: str, records: list[VectorRecord]) -> None:
        """写入或覆盖(同 id 幂等)。"""

    @abstractmethod
    async def search(
        self,
        collection: str,
        vector: list[float],
        *,
        top_k: int,
        filter: str | None = None,
    ) -> list[SearchHit]:
        """相似度检索:按 score 降序返回,至多 top_k 条;filter 为表达式(如 'category == "x"')。"""

    @abstractmethod
    async def delete(self, collection: str, ids: list[str]) -> None:
        """按 id 删除;不存在的 id 静默跳过。"""
