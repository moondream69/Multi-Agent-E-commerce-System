"""缝 3(票 #48):迁移表结构 + PG 投影幂等 —— 真 Postgres 上验证(离线秒 skip)。

- 迁移 0006:``faq`` / ``market_intel`` 溯源列齐(六项)、``market_intel.source`` 放宽、台账表在
- PG 投影幂等:同一批切块 upsert 两次,行数不变(``chunk_id`` 自然键覆盖)—— B27 幂等的 PG 侧
- faq 投影列拆包:问答两段 + locale + tags 各归其列(票 #49 的 FAQ 形态)
- 台账唯一:同 (批次 id, 文档标识) 重记即报错(批次 id 一次摄入一个,重复即暴露)

自清理:本用例只写 ``pgtest-corpus`` 前缀的切块与 ``pgtest-`` 前缀的批次,用完即删——不留残留。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from python_backend.corpus.schema import CorpusChunk
from python_backend.db.corpus_store import PostgresCorpusStore
from python_backend.db.session import SessionFactory
from tests.conftest import require_postgres

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("requires_postgres")]

DOC_ID = "pgtest-corpus"


def _chunks() -> list[CorpusChunk]:
    return [
        CorpusChunk(
            chunk_id=f"{DOC_ID}#0",
            chunk_index=0,
            doc_id=DOC_ID,
            kind="intel",
            title="测试报告",
            source="测试来源",
            published_at=date(2020, 1, 1),
            section="p.1",
            category="行业洞察",
            content="测试正文一。",
        ),
        CorpusChunk(
            chunk_id=f"{DOC_ID}#1",
            chunk_index=1,
            doc_id=DOC_ID,
            kind="intel",
            title="测试报告",
            source="测试来源",
            published_at=date(2020, 1, 1),
            section="pp.1-2",
            category="行业洞察",
            content="测试正文二。",
        ),
    ]


async def _rows(statement: str, **params: object) -> list[tuple]:
    async with SessionFactory() as session:
        return [tuple(row) for row in (await session.execute(text(statement), params)).all()]


async def _columns(table: str) -> dict[str, tuple[str, int | None]]:
    return {
        row[0]: (row[1], row[2])
        for row in await _rows(
            "select column_name, data_type, character_maximum_length from information_schema.columns"
            " where table_name = :table",
            table=table,
        )
    }


@pytest.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    # 先过离线守卫:本夹具的 teardown 触库,离线时不守卫会先付连接超时再报错(而非秒 skip)
    require_postgres()
    yield
    async with SessionFactory() as session, session.begin():
        await session.execute(text("delete from market_intel where doc_id = :doc"), {"doc": DOC_ID})
        await session.execute(text("delete from faq where doc_id = :doc"), {"doc": DOC_ID})
        await session.execute(text("delete from corpus_batches where batch_id like 'pgtest-%'"))


async def test_migration_adds_provenance_columns_and_ledger_table() -> None:
    """迁移 0006:两表溯源列 / 放宽的 source / 台账表都在,且类型正确。"""
    shared = {
        "chunk_id": ("character varying", 80),
        "doc_id": ("character varying", 48),
        "source": ("character varying", 200),
        "published_at": ("date", None),
        "section": ("character varying", 120),
        "chunk_index": ("integer", None),
    }
    for table in ("faq", "market_intel"):
        columns = await _columns(table)
        for name, shape in shared.items():
            assert columns.get(name) == shape, f"{table}.{name} 形状不符:{columns.get(name)}"
    assert (await _columns("market_intel"))["title"] == ("character varying", 300)

    ledger = await _columns("corpus_batches")
    assert ledger["batch_id"] == ("character varying", 36)
    assert ledger["doc_id"] == ("character varying", 48)
    assert ledger["content_hash"] == ("character varying", 64)
    assert ledger["chunk_count"] == ("integer", None)
    assert "ingested_at" in ledger

    unique = await _rows(
        "select tc.table_name from information_schema.table_constraints tc"
        " join information_schema.key_column_usage kcu on kcu.constraint_name = tc.constraint_name"
        " where tc.constraint_type = 'UNIQUE' and kcu.column_name = 'chunk_id'"
    )
    assert {row[0] for row in unique} == {"faq", "market_intel"}, "chunk_id 自然键未建"


async def test_pg_projection_is_idempotent() -> None:
    """同一批切块 upsert 两次:行数不变(覆盖语义);台账逐批次留痕。"""
    store = PostgresCorpusStore()
    chunks = _chunks()

    await store.upsert_chunks("intel", chunks)
    await store.record_batch(batch_id="pgtest-1", doc_id=DOC_ID, content_hash="a" * 64, chunk_count=2)
    await store.upsert_chunks("intel", chunks)
    await store.record_batch(batch_id="pgtest-2", doc_id=DOC_ID, content_hash="a" * 64, chunk_count=2)

    rows = await _rows(
        "select chunk_id, section, chunk_index from market_intel where doc_id = :doc order by chunk_index",
        doc=DOC_ID,
    )
    assert [row[0] for row in rows] == [f"{DOC_ID}#0", f"{DOC_ID}#1"]  # 无重复行
    assert rows[1][1] == "pp.1-2"  # 覆盖后取新值
    batches = await _rows("select batch_id from corpus_batches where doc_id = :doc order by batch_id", doc=DOC_ID)
    assert [row[0] for row in batches] == ["pgtest-1", "pgtest-2"]  # 台账逐批次留痕


async def test_batch_ledger_rejects_same_batch_twice() -> None:
    """同 (批次 id, 文档标识) 重记即报错——批次 id 一次摄入一个。"""
    store = PostgresCorpusStore()
    await store.record_batch(batch_id="pgtest-dup", doc_id=DOC_ID, content_hash="b" * 64, chunk_count=1)

    with pytest.raises(IntegrityError, match=r"uq_corpus_batches_batch_id_doc_id|unique"):
        await store.record_batch(batch_id="pgtest-dup", doc_id=DOC_ID, content_hash="b" * 64, chunk_count=1)


async def test_faq_projection_keeps_locale_and_tags() -> None:
    """faq 投影列拆包:问答两段 + locale + tags 各归其列(票 #49 的 FAQ 形态)。"""
    chunk = CorpusChunk(
        chunk_id=f"{DOC_ID}#0",
        chunk_index=0,
        doc_id=DOC_ID,
        kind="faq",
        title="包裹多久能到?",
        source="自造 FAQ 语料库",
        published_at=date(2026, 9, 14),
        section="物流配送",
        category="物流配送",
        content="Q: 包裹多久能到?\nA: 东南亚一般 5-10 个工作日。",
        question="包裹多久能到?",
        answer="东南亚一般 5-10 个工作日。",
        locale="zh-CN",
        tags=("时效", "物流"),
    )

    await PostgresCorpusStore().upsert_chunks("faq", [chunk])

    rows = await _rows(
        "select question, answer, locale, tags from faq where doc_id = :doc",
        doc=DOC_ID,
    )
    assert rows == [("包裹多久能到?", "东南亚一般 5-10 个工作日。", "zh-CN", ["时效", "物流"])]
