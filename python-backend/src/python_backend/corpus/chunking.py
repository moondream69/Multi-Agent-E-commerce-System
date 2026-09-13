"""缝 1:切块与标识派生(纯函数,不触 PG / Milvus / Ollama)。

策略(ADR-0007「自写切块」,参数 = spec #46 暂存假设 3):
- **intel**:正文按句边界打包,目标 ``MIN_CHUNK_CHARS``~``MAX_CHUNK_CHARS`` 字符,块间重叠 ~12%
  (pypdf 逐行输出 → 先还原折行/断词,再切句;不做版式还原)
- **faq**:一问一答为一个知识单元,**不切**

切块标识 = ``<文档标识>#<序号>`` 确定性派生:重灌同 id 覆盖(幂等),模型引用时有稳定标识可指。
"""

from __future__ import annotations

import re

from python_backend.corpus.schema import CorpusChunk, CorpusDocument, FaqEntry

MIN_CHUNK_CHARS = 500
MAX_CHUNK_CHARS = 800
OVERLAP_RATIO = 0.12

# 句边界:句末标点 + 空白 + 下一句起首(大写字母/数字/引号)。缩写(如 U.S.)会误切,
# 但误切只挪动打包边界、不改内容,代价可接受——不为它引入语言相关的复杂规则。
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def chunk_document(doc: CorpusDocument) -> list[CorpusChunk]:
    if doc.kind == "faq":
        return [_faq_chunk(doc, index, entry) for index, entry in enumerate(doc.entries)]
    return _pack_intel(doc)


def _faq_chunk(doc: CorpusDocument, index: int, entry: FaqEntry) -> CorpusChunk:
    return _chunk(
        doc,
        index,
        title=entry.question,  # 引用面标题 = 问题本身
        section=doc.category,
        content=f"Q: {entry.question}\nA: {entry.answer}",
        question=entry.question,
        answer=entry.answer,
    )


def _pack_intel(doc: CorpusDocument) -> list[CorpusChunk]:
    units = [(page.number, piece) for page in doc.pages for piece in _page_units(page.text)]
    chunks: list[CorpusChunk] = []
    current: list[tuple[int, str]] = []
    current_len = 0
    for unit in units:
        if current and current_len >= MIN_CHUNK_CHARS and current_len + len(unit[1]) > MAX_CHUNK_CHARS:
            chunks.append(_intel_chunk(doc, len(chunks), current))
            current = _overlap_tail(current)
            current_len = sum(len(text) for _, text in current)
        current.append(unit)
        current_len += len(unit[1])
    if current:
        chunks.append(_intel_chunk(doc, len(chunks), current))
    return chunks


def _intel_chunk(doc: CorpusDocument, index: int, units: list[tuple[int, str]]) -> CorpusChunk:
    pages = [page for page, _ in units]
    first, last = min(pages), max(pages)
    return _chunk(
        doc,
        index,
        title=doc.title,
        section=f"p.{first}" if first == last else f"pp.{first}-{last}",
        content=" ".join(text for _, text in units),
    )


def _chunk(
    doc: CorpusDocument,
    index: int,
    *,
    title: str,
    section: str,
    content: str,
    question: str | None = None,
    answer: str | None = None,
) -> CorpusChunk:
    """切块构造的唯一出口:文档级溯源五字段逐块同源,块级字段由调用方给。"""
    return CorpusChunk(
        chunk_id=f"{doc.doc_id}#{index}",
        chunk_index=index,
        doc_id=doc.doc_id,
        kind=doc.kind,
        title=title,
        source=doc.source,
        published_at=doc.published_at,
        section=section,
        category=doc.category,
        content=content,
        question=question,
        answer=answer,
    )


def _overlap_tail(units: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """块间重叠:回带上一块尾部若干句,累计不超过 OVERLAP_RATIO x 目标上限。"""
    budget = int(MAX_CHUNK_CHARS * OVERLAP_RATIO)
    tail: list[tuple[int, str]] = []
    total = 0
    for unit in reversed(units):
        if total + len(unit[1]) > budget:
            break
        tail.insert(0, unit)
        total += len(unit[1])
    return tail


def _page_units(text: str) -> list[str]:
    """一页正文 → 句列表(超长句按词边界硬切,保证单个单元不超过上限)。"""
    return [piece for sentence in _SENTENCE_SPLIT.split(_normalize(text)) if sentence for piece in _clip(sentence)]


def _normalize(text: str) -> str:
    """pypdf 逐行输出 → 连续正文:行尾连字符 + 下行小写视为断词合并,其余行以空格相连。"""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if lines and lines[-1].endswith("-") and line[:1].islower():
            lines[-1] = lines[-1][:-1] + line
        else:
            lines.append(line)
    return " ".join(lines)


def _clip(sentence: str) -> list[str]:
    """超长单元按词边界切到上限以内(单条超长句不会把切块撑爆)。"""
    if len(sentence) <= MAX_CHUNK_CHARS:
        return [sentence]
    pieces: list[str] = []
    current = ""
    for word in sentence.split():
        if current and len(current) + 1 + len(word) > MAX_CHUNK_CHARS:
            pieces.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        pieces.append(current)
    return pieces
