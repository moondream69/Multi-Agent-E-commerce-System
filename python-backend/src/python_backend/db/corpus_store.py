"""语料投影与批次台账(spec #46 A):切块同时落 PG(``faq`` / ``market_intel``)与 Milvus。

语料真源是冻结入仓的 YAML(``docs/corpus/``)——本存储是它的 **PG 投影**:按 ``chunk_id``
自然键 upsert(``ON CONFLICT`` 覆盖,重灌不产生重复行),并按批次留一条台账。

CorpusStore 协议:摄入编排经注入使用(生产 ``PostgresCorpusStore``,离线测试内存替身)。
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from python_backend.corpus.schema import CorpusChunk
from python_backend.db.models import CorpusBatch, Faq, MarketIntel
from python_backend.db.session import SessionFactory


class CorpusStore(Protocol):
    """语料投影协议:幂等写入切块 + 批次台账留痕 + 清尾(issue #54)。"""

    async def upsert_chunks(self, kind: str, chunks: list[CorpusChunk]) -> None:
        """按 chunk_id 覆盖写入(同 id 重灌 = 覆盖,非追加)。"""
        ...

    async def stale_chunk_ids(self, kind: str, doc_id: str, keep_ids: list[str]) -> list[str]:
        """清尾的**读**半边:该文档在投影里已有、但不在 ``keep_ids`` 里的旧块 id。

        顺序约定(见 ``corpus/pipeline.py``):先删向量侧、后删投影侧——投影是清尾的账本,
        任一步失败都能在下次重灌时按它重新认出同一批 id 并重试(两侧删除都幂等)。
        """
        ...

    async def delete_chunks(self, kind: str, ids: list[str]) -> None:
        """清尾的**删**半边:按 chunk_id 删除(不存在的 id 静默跳过)。"""
        ...

    async def record_batch(self, *, batch_id: str, doc_id: str, content_hash: str, chunk_count: int) -> None:
        """记一条语料批次台账(内容哈希 + 切块数)。"""
        ...


def _table_for(kind: str) -> type[Faq] | type[MarketIntel]:
    """kind → 投影表(faq / intel 两分支只在此处判一次)。"""
    return Faq if kind == "faq" else MarketIntel


class PostgresCorpusStore(CorpusStore):
    """PG 实现:投影写入 / 清尾读删 / 台账各一个事务(批次 id 由摄入侧生成,重复批次自然冲突即暴露)。"""

    async def upsert_chunks(self, kind: str, chunks: list[CorpusChunk]) -> None:
        if not chunks:
            return
        table = _table_for(kind)
        rows = [_to_row(chunk) for chunk in chunks]
        statement = insert(table).values(rows)
        async with SessionFactory() as session, session.begin():
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["chunk_id"],
                    set_={key: statement.excluded[key] for key in rows[0] if key != "chunk_id"},
                )
            )

    async def stale_chunk_ids(self, kind: str, doc_id: str, keep_ids: list[str]) -> list[str]:
        table = _table_for(kind)
        conditions = [table.doc_id == doc_id]
        if keep_ids:
            conditions.append(table.chunk_id.not_in(keep_ids))
        # keep_ids 为空即「本文档全部行都算陈旧」——产品路径不会出现(pipeline 拒绝零切块文档)
        statement = select(table.chunk_id).where(*conditions)
        async with SessionFactory() as session:
            return list((await session.execute(statement)).scalars().all())

    async def delete_chunks(self, kind: str, ids: list[str]) -> None:
        if not ids:
            return
        table = _table_for(kind)
        async with SessionFactory() as session, session.begin():
            await session.execute(delete(table).where(table.chunk_id.in_(ids)))

    async def record_batch(self, *, batch_id: str, doc_id: str, content_hash: str, chunk_count: int) -> None:
        async with SessionFactory() as session, session.begin():
            session.add(
                CorpusBatch(batch_id=batch_id, doc_id=doc_id, content_hash=content_hash, chunk_count=chunk_count)
            )


def _to_row(chunk: CorpusChunk) -> dict:
    """切块 → 表行:列名差异在此收口(faq 无 category 列——主题落 section;标题列 = question)。"""
    row = {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "source": chunk.source,
        "published_at": chunk.published_at,
        "section": chunk.section,
        "chunk_index": chunk.chunk_index,
    }
    if chunk.kind == "faq":
        return {
            **row,
            "question": chunk.question,
            "answer": chunk.answer,
            "locale": chunk.locale,
            "tags": list(chunk.tags),
        }
    return {**row, "category": chunk.category, "title": chunk.title, "content": chunk.content}
