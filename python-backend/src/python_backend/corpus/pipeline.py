"""缝 2:摄入编排(切块 → 嵌入 → 双投影 + 批次台账)。

解析(PDF → 逐页正文)由 freeze 步完成并冻结进语料文件;本层只吃真源文件:
一份文档 = 切块 → 嵌入(Ollama bge-m3)→ Milvus upsert(同 id 覆盖)→ PG 投影 + 台账。

**覆盖含清尾**(issue #54):重灌时旧切块里「不在本次切块集」的后段块从两侧删除——文档变短
(重冻结后切块数变少)不留孤儿行;清尾范围严格限本文档 ``doc_id``。清尾在写入**之后**、且
**先删向量侧、后删投影侧**:投影是清尾的账本——任一步失败,下次重灌按投影重新认出同一批 id
并重试(两侧删除都幂等)。零切块文档**直接拒绝**(坏语料不该静默清空该文档的投影)。

失败语义:嵌入不可用即显式上抛,该文档**不产生任何写入**(不静默降级为零向量,ADR-0005)。

边界:**整份文档从语料文件移除**后的孤儿行不做自动 prune——按 ``--corpus <单份>`` 部分摄入时,
全局 prune 会把未参与本次摄入的文档一并误删;需要时另开票定「全量摄入 + 显式 prune 开关」。
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
        if not chunks:
            raise ValueError(f"文档 {document.doc_id} 切不出任何块——坏语料拒绝摄入(不静默清空投影)")
        vectors = await _embed_chunks(embedding, [chunk.content for chunk in chunks])
        await vector.upsert(
            COLLECTIONS[document.kind],
            [
                VectorRecord(id=chunk.chunk_id, vector=vector_, payload=chunk.payload())
                for chunk, vector_ in zip(chunks, vectors, strict=True)
            ],
        )
        await store.upsert_chunks(document.kind, chunks)
        # issue #54 清尾:写入后删掉本文档的旧后段块;先向量侧、后投影侧(投影留作重试账本)
        stale = await store.stale_chunk_ids(document.kind, document.doc_id, [c.chunk_id for c in chunks])
        if stale:
            await vector.delete(COLLECTIONS[document.kind], stale)
            await store.delete_chunks(document.kind, stale)
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
