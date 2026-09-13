"""缝 2:摄入编排(切块 → 嵌入 → 双投影 + 批次台账)。

解析(PDF → 逐页正文)由 freeze 步完成并冻结进语料文件;本层只吃真源文件:
一份文档 = 切块 → 嵌入(Ollama bge-m3)→ Milvus upsert(同 id 覆盖)→ PG 投影 + 台账。

失败语义:嵌入不可用即显式上抛,该文档**不产生任何写入**(不静默降级为零向量,ADR-0005)。

已知边界:覆盖按「同 id」生效——文档**变短**时(重冻结后切块数变少),旧的后段块不会被清尾。
T2 铺语料若涉及同一文档的版本替换,需先按 doc_id 清旧块(VectorRepository 现无按 payload
查询的接口,届时补)。
"""

from __future__ import annotations

from dataclasses import dataclass

from python_backend.corpus.chunking import chunk_document
from python_backend.corpus.schema import CorpusDocument, content_hash
from python_backend.db.corpus_store import CorpusStore
from python_backend.infrastructure.embedding import EmbeddingClient
from python_backend.vector_repo.base import VectorRecord, VectorRepository

# 语料 kind → Milvus 集合名(与既有检索工具的集合名一致:trend_query → market_intel)
COLLECTIONS = {"intel": "market_intel", "faq": "faq"}
# 嵌入分批:Ollama 单请求 60s 超时,整份文档一次发会超时(实测 360 块量级)
EMBED_BATCH_SIZE = 16


@dataclass(frozen=True)
class IngestOutcome:
    doc_id: str
    chunk_count: int
    content_hash: str


async def ingest_documents(
    documents: list[CorpusDocument],
    *,
    embedding: EmbeddingClient,
    vector: VectorRepository,
    store: CorpusStore,
    batch_id: str,
) -> list[IngestOutcome]:
    """逐文档摄入:返回每份文档的切块数与内容哈希(供 CLI 打印与台账核对)。"""
    outcomes: list[IngestOutcome] = []
    for document in documents:
        chunks = chunk_document(document)
        vectors = await _embed_chunks(embedding, [chunk.content for chunk in chunks])
        await vector.upsert(
            COLLECTIONS[document.kind],
            [
                VectorRecord(id=chunk.chunk_id, vector=vector_, payload=chunk.payload())
                for chunk, vector_ in zip(chunks, vectors, strict=True)
            ],
        )
        await store.upsert_chunks(document.kind, chunks)
        digest = content_hash(document)
        await store.record_batch(
            batch_id=batch_id, doc_id=document.doc_id, content_hash=digest, chunk_count=len(chunks)
        )
        outcomes.append(IngestOutcome(doc_id=document.doc_id, chunk_count=len(chunks), content_hash=digest))
    return outcomes


async def _embed_chunks(embedding: EmbeddingClient, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        vectors.extend(await embedding.embed(texts[start : start + EMBED_BATCH_SIZE]))
    return vectors
