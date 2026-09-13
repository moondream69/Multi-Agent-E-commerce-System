"""语料投影与批次台账(spec #46 A):切块同时落 PG(``faq`` / ``market_intel``)与 Milvus。

语料真源是冻结入仓的 YAML(``docs/corpus/``)——本存储是它的 **PG 投影**:按 ``chunk_id``
自然键 upsert(``ON CONFLICT`` 覆盖,重灌不产生重复行),并按批次留一条台账。

CorpusStore 协议:摄入编排经注入使用(生产 ``PostgresCorpusStore``,离线测试内存替身)。
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.dialects.postgresql import insert

from python_backend.corpus.schema import CorpusChunk
from python_backend.db.models import CorpusBatch, Faq, MarketIntel
from python_backend.db.session import SessionFactory


class CorpusStore(Protocol):
    """语料投影协议:幂等写入切块 + 批次台账留痕。"""

    async def upsert_chunks(self, kind: str, chunks: list[CorpusChunk]) -> None:
        """按 chunk_id 覆盖写入(同 id 重灌 = 覆盖,非追加)。"""
        ...

    async def record_batch(self, *, batch_id: str, doc_id: str, content_hash: str, chunk_count: int) -> None:
        """记一条语料批次台账(内容哈希 + 切块数)。"""
        ...


class PostgresCorpusStore(CorpusStore):
    """PG 实现:投影与台账各一个事务(批次 id 由摄入侧生成,重复批次自然冲突即暴露)。"""

    async def upsert_chunks(self, kind: str, chunks: list[CorpusChunk]) -> None:
        if not chunks:
            return
        table = Faq if kind == "faq" else MarketIntel
        rows = [_to_row(chunk) for chunk in chunks]
        statement = insert(table).values(rows)
        async with SessionFactory() as session, session.begin():
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["chunk_id"],
                    set_={key: statement.excluded[key] for key in rows[0] if key != "chunk_id"},
                )
            )

    async def record_batch(self, *, batch_id: str, doc_id: str, content_hash: str, chunk_count: int) -> None:
        async with SessionFactory() as session, session.begin():
            session.add(
                CorpusBatch(batch_id=batch_id, doc_id=doc_id, content_hash=content_hash, chunk_count=chunk_count)
            )


def _to_row(chunk: CorpusChunk) -> dict:
    """切块 → 表行:列名差异在此收口(faq 的标题列 = question,正文列 = question/answer)。"""
    row = {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "source": chunk.source,
        "published_at": chunk.published_at,
        "section": chunk.section,
        "chunk_index": chunk.chunk_index,
        "category": chunk.category,
    }
    if chunk.kind == "faq":
        return {**row, "question": chunk.question, "answer": chunk.answer}
    return {**row, "title": chunk.title, "content": chunk.content}
