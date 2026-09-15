"""缝 1(票 #58):语料版本锚——指纹确定性重算 + 「覆盖整库」批次选取(离线纯逻辑)。

真源 = 仓内 ``docs/corpus/*.yaml``(离线可读);台账读经注入假件,不触库。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from python_backend.corpus.schema import CorpusDocument, load_corpus
from python_backend.evals.corpus_anchor import corpus_anchor, corpus_fingerprint, latest_full_batch

CORPUS_DIR = Path(__file__).resolve().parents[2] / "docs" / "corpus"


def _documents() -> list[CorpusDocument]:
    return load_corpus([CORPUS_DIR / "faq.yaml"])


def test_fingerprint_is_deterministic_and_order_free() -> None:
    """同一份真源永远同一指纹:重算确定、与文件/文档顺序无关(排序后再聚合)。"""
    documents = _documents()
    assert corpus_fingerprint(documents) == corpus_fingerprint(documents)
    assert corpus_fingerprint(documents) == corpus_fingerprint(list(reversed(documents)))


def test_fingerprint_changes_with_content() -> None:
    """投影面任一处改动(标题也进投影)即换指纹——否则「同指纹不同语料」会让版本绑定误判。"""
    documents = _documents()
    mutated = [replace(documents[0], title=documents[0].title + "(改)"), *documents[1:]]
    assert corpus_fingerprint(mutated) != corpus_fingerprint(documents)


def test_latest_full_batch_picks_the_latest_covering_batch() -> None:
    """行序 = ingested_at 升序(batch_id 是 uuid4,不可比大小):取**最后一个**覆盖整库的批次。"""
    rows = [("b1", "doc-a"), ("b1", "doc-b"), ("b2", "doc-a"), ("b3", "doc-a"), ("b3", "doc-b")]
    assert latest_full_batch(rows, doc_ids={"doc-a", "doc-b"}) == "b3"
    # 部分摄入批次不冒充整库版本:尾部只覆盖 doc-a 的两批都不算
    assert latest_full_batch(rows[:4], doc_ids={"doc-a", "doc-b"}) == "b1"
    assert latest_full_batch([("b9", "doc-a")], doc_ids={"doc-a", "doc-b"}) is None
    assert latest_full_batch([], doc_ids={"doc-a"}) is None
    assert latest_full_batch(rows, doc_ids=set()) is None


async def test_corpus_anchor_keeps_fingerprint_when_ledger_unreadable() -> None:
    """台账读不到 → 指纹照给、批次留空、note 记原因(不阻断跑批,如实标注)。"""

    async def broken() -> list[tuple[str, str]]:
        raise RuntimeError("库不可达")

    anchor = await corpus_anchor([CORPUS_DIR / "faq.yaml"], read_rows=broken)
    assert len(anchor.fingerprint) == 64
    assert anchor.batch_id is None
    assert "库不可达" in anchor.note


async def test_corpus_anchor_reads_batch_from_ledger() -> None:
    """台账行覆盖真源全部文档 → 批次附记落位,note 空。"""

    async def rows() -> list[tuple[str, str]]:
        return [("batch-1", document.doc_id) for document in _documents()]

    anchor = await corpus_anchor([CORPUS_DIR / "faq.yaml"], read_rows=rows)
    assert anchor.batch_id == "batch-1"
    assert anchor.note == ""


async def test_corpus_anchor_notes_partial_ledger() -> None:
    """台账只有部分摄入批次 → 批次留空 + note 说明(不拿部分批次冒充整库版本)。"""

    async def rows() -> list[tuple[str, str]]:
        return [("b1", _documents()[0].doc_id)]

    anchor = await corpus_anchor([CORPUS_DIR / "faq.yaml"], read_rows=rows)
    assert anchor.batch_id is None
    assert "覆盖整库" in anchor.note
