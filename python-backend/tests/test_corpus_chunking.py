"""缝 1(spec #46 A / 票 #48):切块与标识派生 —— 纯函数,不触 PG / Milvus / Ollama。

票面「在缝上测」三项:
- 确定性:同输入两次调用,切块 id 集合完全相同(幂等的地基)
- FAQ 单元:一问一答不被切分(一条 Q&A 进来,一个切块出去)
- 长度:切块落在约定区间(断言用区间、不写死数值——策略会调)

另测语料文件 schema(真源 YAML 的读写形状):字段齐、派生字段(切块序号/标识)由代码补。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from python_backend.corpus.chunking import (
    MAX_CHUNK_CHARS,
    MIN_CHUNK_CHARS,
    chunk_document,
)
from python_backend.corpus.schema import content_hash, load_corpus, save_document
from tests.conftest import corpus_faq_doc, corpus_intel_doc

# —— 在缝上测 ——


def test_chunk_ids_are_deterministic() -> None:
    """确定性:同输入两次调用,切块 id 集合完全相同——重灌覆盖语义的地基。"""
    doc = corpus_intel_doc()
    first = chunk_document(doc)
    second = chunk_document(doc)

    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert [c.content for c in first] == [c.content for c in second]
    assert len({c.chunk_id for c in first}) == len(first)  # 无重复标识
    assert first  # 非空,否则下列断言恒真


def test_chunk_id_derives_from_doc_id_and_index() -> None:
    """标识确定性派生:<文档标识>#<序号>——模型引用时有稳定标识可指。"""
    chunks = chunk_document(corpus_intel_doc())

    assert [c.chunk_id for c in chunks] == [f"demo-report#{i}" for i in range(len(chunks))]
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_faq_entry_is_not_split() -> None:
    """FAQ 单元:一问一答进来,一个切块出去(不切)。"""
    chunks = chunk_document(corpus_faq_doc())

    assert len(chunks) == len(corpus_faq_doc().entries)
    assert [c.chunk_id for c in chunks] == ["faq-logistics#0", "faq-logistics#1"]
    assert "包裹多久能到?" in chunks[0].content
    assert "东南亚一般 5-10 个工作日。" in chunks[0].content


def test_chunk_length_within_agreed_band() -> None:
    """长度:切块落在约定区间(区间取自模块常量——调参不该让测试全红)。"""
    chunks = chunk_document(corpus_intel_doc())

    assert all(len(c.content) <= MAX_CHUNK_CHARS * 1.5 for c in chunks)
    # 唯一允许的短块是末块(尾段凑不满);其余按构造 ≥ 下限
    assert all(len(c.content) >= MIN_CHUNK_CHARS for c in chunks[:-1])


def test_chunks_carry_six_field_provenance() -> None:
    """六项完整溯源进 payload:文档标识 / 标题 / 来源渠道 / 发布日期 / 章节或页码 / 切块序号。"""
    payload = chunk_document(corpus_intel_doc())[0].payload()

    assert payload["doc_id"] == "demo-report"
    assert payload["title"] == "Demo Report"
    assert payload["source"] == "Example Publisher (CC BY)"
    assert payload["published_at"] == "2020-01-01"
    assert payload["section"].startswith("p.")  # intel 侧以页码为章节归属
    assert payload["chunk_index"] == 0
    assert payload["content"].strip()


def test_intel_chunks_track_page_numbers() -> None:
    """页码溯源:第一块属第 1 页;跨页块标注页码范围。"""
    chunks = chunk_document(corpus_intel_doc(pages=3))

    assert chunks[0].section == "p.1"
    assert any("-" in c.section for c in chunks)  # 长文必然跨页


def test_faq_chunk_keeps_question_and_answer_parts() -> None:
    """FAQ 切块另带问答两段与语种/标签(PG 投影的 question/answer/locale/tags 列直接取用,不在存储层反解正文)。"""
    [chunk, _] = chunk_document(corpus_faq_doc())

    assert chunk.question == "包裹多久能到?"
    assert chunk.answer == "东南亚一般 5-10 个工作日。"
    assert chunk.locale == "zh-CN"  # 语料只造中文(ADR-0007:bge-m3 跨语言检索)
    assert chunk.tags == ("时效",)


def test_content_hash_covers_projection_fields() -> None:
    """台账哈希锁「投影面」:标签/语种这类会进投影的字段改了,哈希必须跟着变(否则同哈希不同投影)。"""
    doc = corpus_faq_doc()
    [first, second] = doc.entries
    retagged = dataclasses.replace(doc, entries=(first, dataclasses.replace(second, tags=["换了个标签"])))

    assert content_hash(doc) == content_hash(doc)  # 同输入同哈希
    assert content_hash(doc) != content_hash(retagged)


# —— 语料文件 schema(真源) ——


def test_load_corpus_reads_intel_and_faq_documents(tmp_path: Path) -> None:
    """语料文件 = 真源:YAML 读回(含逐页正文),派生字段由代码补。"""
    path = tmp_path / "corpus.yaml"
    path.write_text(
        """
version: 1
documents:
  - doc_id: demo-report
    kind: intel
    title: Demo Report
    source: Example Publisher (CC BY)
    published_at: '2020-01-01'
    category: 行业洞察
    url: https://example.com/report.pdf
    license_note: 公开可得、许可允许转载(复核用一句话)
    source_sha256: abc
    pages:
      - page: 1
        text: |-
          First page body.
  - doc_id: faq-logistics
    kind: faq
    title: 物流配送 FAQ
    source: 自造 FAQ 语料库
    published_at: '2026-09-14'
    category: 物流配送
    entries:
      - question: 包裹多久能到?
        answer: 东南亚一般 5-10 个工作日。
        locale: zh-CN
        tags: [时效]
""",
        encoding="utf-8",
    )

    docs = load_corpus([path])

    assert [d.doc_id for d in docs] == ["demo-report", "faq-logistics"]
    assert docs[0].pages[0].text == "First page body."
    assert docs[0].license_note == "公开可得、许可允许转载(复核用一句话)"
    assert docs[1].entries[0].question == "包裹多久能到?"
    assert docs[1].entries[0].locale == "zh-CN"
    assert docs[1].entries[0].tags == ["时效"]


def test_load_corpus_rejects_overlong_doc_id(tmp_path: Path) -> None:
    """文档标识上限:Milvus 字符串主键 64 字符(``<doc_id>#<序号>`` 须装得下)。"""
    path = tmp_path / "corpus.yaml"
    path.write_text(
        f"""
documents:
  - doc_id: {"x" * 60}
    kind: faq
    title: t
    source: s
    published_at: '2026-09-14'
    category: c
    entries:
      - question: q
        answer: a
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="doc_id"):
        load_corpus([path])


def test_save_document_round_trips_and_keeps_other_documents(tmp_path: Path) -> None:
    """写形状与读形状同源:存回的文档可原样读回;同 doc_id 覆盖、文件内其余文档不动。"""
    path = tmp_path / "market-intel.yaml"
    save_document(path, corpus_intel_doc())
    save_document(path, corpus_faq_doc())
    save_document(path, corpus_intel_doc())  # 覆盖写

    docs = load_corpus([path])

    assert [d.doc_id for d in docs] == ["faq-logistics", "demo-report"]  # 覆盖不重复、他文档保留
    stored = next(d for d in docs if d.doc_id == "demo-report")
    assert stored.pages[0].text == corpus_intel_doc().pages[0].text  # 多行正文原样往返
    assert "text: |-" in path.read_text(encoding="utf-8")  # 字面块落盘(可读可 diff)
    assert "locale: zh-CN" in path.read_text(encoding="utf-8")  # 语种随条目落盘(真源不丢形态)
