"""冻结语料文件入仓契约(票 #49):``docs/corpus/*.yaml`` 是真源,覆盖面与切块口径由用例守卫。

离线纯函数(不触 PG / Milvus / Ollama)——它是投影的上游,坏了先在这里红。
- 情报四类齐 / FAQ 七主题齐(票 #49 的「铺开」口径)
- FAQ 一问一答 = 一个切块单元,不切分(票面 AC)
"""

from __future__ import annotations

from pathlib import Path

from python_backend.corpus.chunking import chunk_document
from python_backend.corpus.schema import CorpusDocument, load_corpus

CORPUS_DIR = Path(__file__).resolve().parents[2] / "docs" / "corpus"
INTEL_CATEGORIES = {"趋势分析", "竞品分析", "季节性规律", "行业洞察"}
FAQ_TOPICS = {"物流配送", "退货退款", "支付方式", "关税清关", "质量真伪", "尺码适配", "售后保修"}


def _documents(kind: str) -> list[CorpusDocument]:
    return [doc for doc in load_corpus(sorted(CORPUS_DIR.glob("*.yaml"))) if doc.kind == kind]


def test_intel_corpus_covers_four_categories() -> None:
    """情报四类齐:每类至少一份真实报告,且逐份带取材凭证(直链 + 条款依据)。"""
    documents = _documents("intel")

    assert {doc.category for doc in documents} >= INTEL_CATEGORIES, "情报类别未铺满四类"
    for doc in documents:
        assert doc.pages, f"{doc.doc_id} 无正文"
        assert doc.url, f"{doc.doc_id} 缺来源直链"
        assert doc.license_note, f"{doc.doc_id} 缺条款依据(复核用)"


def test_faq_corpus_covers_seven_topics() -> None:
    """FAQ 七主题齐,每主题不少于 10 条(旧系统规模:七主题共 ~100 条),且逐份带条款依据。"""
    documents = _documents("faq")

    assert {doc.category for doc in documents} == FAQ_TOPICS
    counts = {doc.category: len(doc.entries) for doc in documents}
    assert all(count >= 10 for count in counts.values()), counts
    assert sum(counts.values()) >= 100, counts
    for doc in documents:
        assert all(entry.locale == "zh-CN" for entry in doc.entries), f"{doc.doc_id} 混入非中文条目"
        assert doc.license_note, f"{doc.doc_id} 缺条款依据(自造语料须写明不涉第三方权利)"


def test_every_faq_entry_is_exactly_one_chunk() -> None:
    """一问一答 = 一个切块单元(不切分):切块数 == 条目数,且正文含完整问答两段。"""
    documents = _documents("faq")

    for doc in documents:
        chunks = chunk_document(doc)
        assert len(chunks) == len(doc.entries), f"{doc.doc_id}:切块数与条目数不符"
        for chunk, entry in zip(chunks, doc.entries, strict=True):
            assert chunk.content == f"Q: {entry.question}\nA: {entry.answer}"
            assert chunk.question == entry.question and chunk.answer == entry.answer
            assert chunk.section == doc.category  # 溯源:FAQ 的「章节或页码」= 主题
