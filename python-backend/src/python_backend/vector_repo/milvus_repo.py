"""Milvus Standalone 实现(术语表:VectorRepository 的当前实现;pgvector 可切换退路)。

- payload 经 dynamic field 存取(enable_dynamic_field),集合按 settings.embedding_dimension 懒创建
- 同步 pymilvus 客户端以 asyncio.to_thread 包裹,不阻塞事件循环(单进程 asyncio 模型)
- 度量 COSINE:search 返回的 distance 即相似度(大=近),与接口 score 语义一致
"""

from __future__ import annotations

import asyncio

from pymilvus import MilvusClient

from python_backend.settings import get_settings
from python_backend.vector_repo.base import SearchHit, VectorRecord, VectorRepository


class MilvusVectorRepository(VectorRepository):
    def __init__(self, *, client: MilvusClient | None = None, dimension: int | None = None) -> None:
        self._client: MilvusClient | None = client
        self._dimension = dimension or get_settings().embedding_dimension

    @property
    def client(self) -> MilvusClient:
        """惰性建连:构造仓库不要求 Milvus 在线(离线装配与 CI 快速套件依赖此点)。"""
        if self._client is None:
            self._client = MilvusClient(uri=get_settings().milvus_uri)
        return self._client

    def _ensure_collection(self, collection: str) -> None:
        if not self.client.has_collection(collection):
            self.client.create_collection(
                collection_name=collection,
                dimension=self._dimension,
                primary_field_name="id",
                id_type="string",
                max_length=64,  # string 主键必填(max_length 经 kwargs 传给 VARCHAR 字段)
                metric_type="COSINE",
                enable_dynamic_field=True,
                consistency_level="Strong",  # 软删除与写入对检索立即可见(契约:写入立即可查、删除立即可排除)
            )

    async def upsert(self, collection: str, records: list[VectorRecord]) -> None:
        if not records:
            return
        self._ensure_collection(collection)

        def _upsert_and_flush() -> None:
            self.client.upsert(
                collection_name=collection,
                data=[{"id": r.id, "vector": r.vector, **r.payload} for r in records],
            )
            self.client.flush(collection_name=collection)  # Milvus 写入须 flush 后才可检索(契约:写入立即可查)

        await asyncio.to_thread(_upsert_and_flush)

    async def search(
        self, collection: str, vector: list[float], *, top_k: int, filter: str | None = None
    ) -> list[SearchHit]:
        self._ensure_collection(collection)
        kwargs: dict = {}
        if filter:  # pymilvus 不接受 None,而空串 "" 会被解释为过滤一切的表达式——无过滤须不传该参数
            kwargs["filter"] = filter
        results = await asyncio.to_thread(
            self.client.search,
            collection_name=collection,
            data=[vector],
            limit=top_k,
            output_fields=["*"],
            consistency_level="Strong",  # 与集合级一致:已删除记录不进检索结果
            **kwargs,
        )
        hits: list[SearchHit] = []
        for hit in results[0]:
            entity = hit.get("entity") or {}
            payload = {k: v for k, v in entity.items() if k not in ("id", "vector")}
            hits.append(SearchHit(id=str(hit["id"]), score=hit["distance"], payload=payload))
        return hits

    async def delete(self, collection: str, ids: list[str]) -> None:
        if not ids or not self.client.has_collection(collection):
            return

        def _delete_and_flush() -> None:
            self.client.delete(collection_name=collection, ids=ids)
            self.client.flush(collection_name=collection)  # 软删除标记同样须 flush 后对检索生效

        await asyncio.to_thread(_delete_and_flush)
