"""缝 2(spec #46 A / 票 #48):摄入编排 —— 替身注入,不触 PG / Milvus / Ollama。

票面「在缝上测」三项:
- 一次摄入调用 upsert,记录数 == 切块数
- 重跑同一份文档,upsert 收到的是相同 id 集合(覆盖语义,不是追加)—— B27 幂等的地基
- 语料批次台账写了一条(注入替身断言)

外加:嵌入不可用须显式失败且不留半写(ADR-0005 行为:不静默降级为零向量)。
"""

from __future__ import annotations

import pytest

from python_backend.corpus.pipeline import ingest_documents
from tests.conftest import (
    CORPUS_PAGE_TEXT,
    FakeEmbedding,
    InMemoryCorpusStore,
    InMemoryVectorRepository,
    corpus_faq_doc,
    corpus_intel_doc,
)


class _FailingEmbedding:
    """嵌入不可用替身(离线复现 Ollama 掉线)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("Embedding 服务不可用")


async def _collection_ids(vector: InMemoryVectorRepository, collection: str, query_text: str) -> set[str]:
    """经公开检索面观察某集合的 id 集合(search 是契约的一部分,不摸替身内部)。"""
    [query] = await FakeEmbedding().embed([query_text])
    hits = await vector.search(collection, query, top_k=10_000)
    return {hit.id for hit in hits}


async def test_ingest_upserts_one_record_per_chunk() -> None:
    """一次摄入 = 一次 upsert,记录数 == 切块数。"""
    vector, store = InMemoryVectorRepository(), InMemoryCorpusStore()

    await ingest_documents(
        [corpus_intel_doc()], embedding=FakeEmbedding(), vector=vector, store=store, batch_id="batch-1"
    )

    ids = await _collection_ids(vector, "market_intel", CORPUS_PAGE_TEXT)
    assert len(ids) == len(store.chunks)
    assert len(ids) > 1  # 语料确实被切成了多块,否则「记录数 == 切块数」恒真


async def test_rerun_reuses_same_chunk_ids() -> None:
    """重跑同一份文档:同一 id 集合(覆盖语义,不是追加)—— B27 幂等。"""
    vector, store = InMemoryVectorRepository(), InMemoryCorpusStore()
    embedding = FakeEmbedding()

    await ingest_documents([corpus_intel_doc()], embedding=embedding, vector=vector, store=store, batch_id="batch-1")
    first_ids = await _collection_ids(vector, "market_intel", CORPUS_PAGE_TEXT)
    first_chunk_ids = set(store.chunks)

    await ingest_documents([corpus_intel_doc()], embedding=embedding, vector=vector, store=store, batch_id="batch-2")
    second_ids = await _collection_ids(vector, "market_intel", CORPUS_PAGE_TEXT)

    assert second_ids == first_ids  # 集合未膨胀(覆盖,非追加)
    assert set(store.chunks) == first_chunk_ids


async def test_ingest_records_batch_ledger_row() -> None:
    """语料批次台账:每次摄入留痕一条(批次 id / 文档标识 / 内容哈希 / 切块数)。"""
    vector, store = InMemoryVectorRepository(), InMemoryCorpusStore()

    [outcome] = await ingest_documents(
        [corpus_intel_doc()], embedding=FakeEmbedding(), vector=vector, store=store, batch_id="batch-1"
    )

    assert len(store.batches) == 1
    row = store.batches[0]
    assert row["batch_id"] == "batch-1"
    assert row["doc_id"] == "demo-report"
    assert row["chunk_count"] == len(store.chunks) == outcome.chunk_count
    assert len(row["content_hash"]) == 64  # sha256 十六进制


async def test_faq_document_goes_to_faq_collection() -> None:
    """kind → 集合路由:faq 文档进 faq 集合(问答各成一块,不切)。"""
    vector, store = InMemoryVectorRepository(), InMemoryCorpusStore()

    await ingest_documents(
        [corpus_faq_doc()], embedding=FakeEmbedding(), vector=vector, store=store, batch_id="batch-1"
    )

    assert await _collection_ids(vector, "faq", "包裹多久能到?") == {"faq-logistics#0", "faq-logistics#1"}
    assert await _collection_ids(vector, "market_intel", "包裹多久能到?") == set()


async def test_embedding_failure_aborts_without_writes() -> None:
    """嵌入不可用:显式失败,且不留半写(不静默降级为零向量)。"""
    vector, store = InMemoryVectorRepository(), InMemoryCorpusStore()

    with pytest.raises(RuntimeError, match="Embedding 服务不可用"):
        await ingest_documents(
            [corpus_intel_doc()], embedding=_FailingEmbedding(), vector=vector, store=store, batch_id="batch-1"
        )

    assert await _collection_ids(vector, "market_intel", CORPUS_PAGE_TEXT) == set()
    assert store.batches == []


async def test_zero_chunk_document_is_rejected_without_touching_projections() -> None:
    """#54:零切块文档(坏语料)直接拒绝 —— 不静默清空该文档的投影(keep 为空的清尾路径不可达)。"""
    vector, store = InMemoryVectorRepository(), InMemoryCorpusStore()
    embedding = FakeEmbedding()
    await ingest_documents(
        [corpus_intel_doc(pages=1)], embedding=embedding, vector=vector, store=store, batch_id="batch-1"
    )
    before = set(store.chunks)

    with pytest.raises(ValueError, match="切不出任何块"):
        await ingest_documents(
            [corpus_intel_doc(pages=0)], embedding=embedding, vector=vector, store=store, batch_id="batch-2"
        )

    assert set(store.chunks) == before  # 投影未动
    assert await _collection_ids(vector, "market_intel", CORPUS_PAGE_TEXT) == before


async def test_shorter_document_drops_stale_tail_chunks() -> None:
    """#54:同一文档「先长后短」重灌 —— 旧的后段块两侧清掉,不留孤儿行。"""
    vector, store = InMemoryVectorRepository(), InMemoryCorpusStore()
    embedding = FakeEmbedding()

    await ingest_documents(
        [corpus_intel_doc(pages=3)], embedding=embedding, vector=vector, store=store, batch_id="batch-long"
    )
    long_ids = await _collection_ids(vector, "market_intel", CORPUS_PAGE_TEXT)
    assert long_ids == set(store.chunks)  # 前置:长版本两侧一致

    await ingest_documents(
        [corpus_intel_doc(pages=1)], embedding=embedding, vector=vector, store=store, batch_id="batch-short"
    )

    short_ids = await _collection_ids(vector, "market_intel", CORPUS_PAGE_TEXT)
    assert short_ids < long_ids  # 确实变短(否则本用例恒真)
    assert short_ids == set(store.chunks)  # 两侧同集合:Milvus / PG 都无孤儿行
    assert len(store.batches) == 2  # 台账逐批次留痕


async def test_stale_cleanup_leaves_other_documents_untouched() -> None:
    """#54:清尾严格限定本文档 —— 同集合里的其他文档一块不少。"""
    vector, store = InMemoryVectorRepository(), InMemoryCorpusStore()
    embedding = FakeEmbedding()

    await ingest_documents(
        [corpus_intel_doc("doc-a", pages=2), corpus_intel_doc("doc-b", pages=2)],
        embedding=embedding,
        vector=vector,
        store=store,
        batch_id="batch-1",
    )
    other_ids = {chunk_id for chunk_id in store.chunks if chunk_id.startswith("doc-b#")}
    assert len(other_ids) > 1  # 前置:doc-b 确实多块(否则「未误伤」恒真)

    await ingest_documents(
        [corpus_intel_doc("doc-a", pages=1)], embedding=embedding, vector=vector, store=store, batch_id="batch-2"
    )

    ids = await _collection_ids(vector, "market_intel", CORPUS_PAGE_TEXT)
    stored = set(store.chunks)
    assert {id_ for id_ in ids if id_.startswith("doc-b#")} == other_ids
    assert {id_ for id_ in stored if id_.startswith("doc-b#")} == other_ids
    assert {id_ for id_ in ids if id_.startswith("doc-a#")} == {id_ for id_ in stored if id_.startswith("doc-a#")}
