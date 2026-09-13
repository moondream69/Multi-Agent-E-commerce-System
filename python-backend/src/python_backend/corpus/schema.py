"""语料文件 schema(spec #46 A「双语料文件真源」):冻结入仓的 YAML 是语料真源。

PG(`faq` / `market_intel`)与 Milvus 都是它的投影——重灌以文件为输入,可 diff、可版本化。

文件形状(version 1)::

    version: 1
    documents:
      - doc_id: <稳定标识,≤48 字符>          # 切块标识 = <doc_id>#<序号>
        kind: intel | faq
        title: <标题>                          # 溯源:标题
        source: <来源渠道>                      # 溯源:来源渠道(报告出版方 / 自造语料库)
        published_at: 'YYYY-MM-DD'             # 溯源:发布日期
        category: <情报四类之一 | FAQ 七主题之一>
        # —— 以下 intel 侧取材凭证(直链、署名、条款依据、源文件哈希) ——
        url: <直链>
        attribution: <署名/许可文本>
        license_note: <条款依据一句话>          # 复核用:为什么这份材料可收
        source_sha256: <源 PDF 字节哈希>
        pages:                                 # intel:逐页正文(pypdf 抽取后冻结)
          - page: 1
            text: |-
              ...
        # —— 或 faq 侧 ——
        entries:                               # faq:一问一答为一个知识单元(不切)
          - question: ...
            answer: ...
            locale: zh-CN                      # 语种(暂只造中文语料;bge-m3 跨语言检索)
            tags: [...]
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

# 切块标识 = f"{doc_id}#{index}" 须装进 Milvus 字符串主键(max_length=64,见 MilvusVectorRepository)
DOC_ID_MAX_LEN = 48
KINDS = ("intel", "faq")


@dataclass(frozen=True)
class Page:
    """intel 侧一页正文(页码用于切块的「章节或页码」溯源)。"""

    number: int
    text: str


@dataclass(frozen=True)
class FaqEntry:
    question: str
    answer: str
    locale: str = "zh-CN"  # 语料只造中文(ADR-0007:bge-m3 跨语言检索,不造多语)
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CorpusDocument:
    """语料文件里的一份文档:kind 决定正文取 pages(intel)还是 entries(faq)。"""

    doc_id: str
    kind: str
    title: str
    source: str
    published_at: date
    category: str
    pages: tuple[Page, ...] = ()
    entries: tuple[FaqEntry, ...] = ()
    url: str | None = None
    attribution: str | None = None
    license_note: str | None = None
    source_sha256: str | None = None


@dataclass(frozen=True)
class CorpusChunk:
    """一个切块 + 六项完整溯源(Milvus payload 与 PG 行共用的形状)。

    ``content`` 是喂嵌入与供引用的正文;faq 侧另有问答两段(``question`` / ``answer``)
    与语种/标签,供 PG 投影的 question/answer/locale/tags 列直接取用(不在存储层反解正文)。
    """

    chunk_id: str
    chunk_index: int
    doc_id: str
    kind: str
    title: str
    source: str
    published_at: date
    section: str  # 章节或页码(intel=页码范围;faq=主题)
    category: str
    content: str
    question: str | None = None
    answer: str | None = None
    locale: str | None = None
    tags: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "source": self.source,
            "published_at": self.published_at.isoformat(),
            "section": self.section,
            "chunk_index": self.chunk_index,
            "category": self.category,
            "content": self.content,
        }


def load_corpus(paths: list[Path]) -> list[CorpusDocument]:
    """读语料文件(真源)→ 文档列表;形状非法即报错(坏语料不许静默入库)。"""
    documents: list[CorpusDocument] = []
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for entry in raw.get("documents", []):
            documents.append(_build_document(entry, path))
    return documents


def save_document(path: Path, document: CorpusDocument) -> None:
    """把一份文档写进语料文件(真源):同 doc_id 覆盖,文件内其余文档保持不动。

    读写同一模块的同一份形状约定(freeze 的产出即可被 load_corpus 读回)。
    """
    existing = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    documents = [doc for doc in (existing or {}).get("documents", []) if doc.get("doc_id") != document.doc_id]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            {"version": 1, "documents": [*documents, _to_entry(document)]},
            Dumper=_LiteralDumper,
            allow_unicode=True,
            sort_keys=False,
            width=1000,  # 长行不折(正文在字面块里,折了反而难 diff)
        ),
        encoding="utf-8",
    )


def content_hash(document: CorpusDocument) -> str:
    """文档内容哈希(sha256):覆盖**投影面**(文档级溯源字段 + 正文/问答单元),任一处改动即换哈希。

    语料批次台账据此绑定版本(评测跑批记录当此哈希)。改标签、语种这类不进正文却进投影的字段
    同样换哈希——否则「同哈希不同投影」会让版本绑定误判为同一版。
    """
    header = "\n".join(
        (
            document.doc_id,
            document.kind,
            document.title,
            document.source,
            document.published_at.isoformat(),
            document.category,
        )
    )
    if document.kind == "faq":
        body = "\n".join(
            f"{entry.question}\n{entry.answer}\n{entry.locale}\n{','.join(entry.tags)}" for entry in document.entries
        )
    else:
        body = "\n".join(f"{page.number}\n{page.text}" for page in document.pages)
    return hashlib.sha256(f"{header}\n{body}".encode()).hexdigest()


class _LiteralDumper(yaml.SafeDumper):
    """逐页正文以字面块(|)输出——语料文件要可读、可 diff,不能是一墙转义符。"""


def _represent_multiline(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|" if "\n" in data else None)


_LiteralDumper.add_representer(str, _represent_multiline)


def _to_entry(document: CorpusDocument) -> dict[str, Any]:
    """文档 → 语料文件条目(空值不落盘:缺 url / attribution 时不写 null)。"""
    entry: dict[str, Any] = {
        "doc_id": document.doc_id,
        "kind": document.kind,
        "title": document.title,
        "source": document.source,
        "published_at": document.published_at.isoformat(),
        "category": document.category,
    }
    for name in ("url", "attribution", "license_note", "source_sha256"):
        value = getattr(document, name)
        if value is not None:
            entry[name] = value
    if document.pages:
        entry["pages"] = [{"page": page.number, "text": page.text} for page in document.pages]
    if document.entries:
        entry["entries"] = [
            {
                "question": item.question,
                "answer": item.answer,
                "locale": item.locale,
                "tags": item.tags,
            }
            for item in document.entries
        ]
    return entry


def _build_document(entry: dict[str, Any], path: Path) -> CorpusDocument:
    doc_id = str(entry.get("doc_id", ""))
    if not doc_id or len(doc_id) > DOC_ID_MAX_LEN or "#" in doc_id:
        raise ValueError(f"{path}:doc_id 非法({doc_id!r};须非空、不含 '#'、≤{DOC_ID_MAX_LEN} 字符)")
    kind = entry.get("kind")
    if kind not in KINDS:
        raise ValueError(f"{path}:{doc_id} 的 kind 非法({kind!r};可选 {KINDS})")
    pages = tuple(Page(number=int(page["page"]), text=str(page["text"]).rstrip()) for page in entry.get("pages", []))
    entries = tuple(
        FaqEntry(
            question=str(item["question"]),
            answer=str(item["answer"]),
            locale=str(item.get("locale", "zh-CN")),
            tags=list(item.get("tags", [])),
        )
        for item in entry.get("entries", [])
    )
    if kind == "intel" and not pages:
        raise ValueError(f"{path}:{doc_id} 缺 pages(intel 文档须带逐页正文)")
    if kind == "faq" and not entries:
        raise ValueError(f"{path}:{doc_id} 缺 entries(faq 文档须带问答单元)")
    return CorpusDocument(
        doc_id=doc_id,
        kind=kind,
        title=str(entry.get("title", "")),
        source=str(entry.get("source", "")),
        published_at=date.fromisoformat(str(entry["published_at"])),
        category=str(entry.get("category", "")),
        pages=pages,
        entries=entries,
        url=entry.get("url"),
        attribution=entry.get("attribution"),
        license_note=entry.get("license_note"),
        source_sha256=entry.get("source_sha256"),
    )
