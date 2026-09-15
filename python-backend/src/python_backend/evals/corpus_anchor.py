"""语料版本锚(spec #55 B / ADR-0008):内容哈希指纹为**主锚** + batch_id **尽力附记**。

指纹由语料真源**离线重算**(``docs/corpus/*.yaml`` → 逐文档内容哈希 → 聚合):确定性、不依赖任何库,
净库重建后照样可算——历史分数据此归因语料版本(ADR-0007「评测对接」段的义务)。

``batch_id`` 只活在 PG 摄入台账(``corpus_batches``),且无「当前批次」语义(部分摄入批次会冒充最新),
故只作附记:从**摄入所指库**(settings 指向的库 = 摄入 CLI 同源 ``.env``;评测净库不在此列——它没灌过
语料)读「覆盖整库」的最近批次。读不到(库不可达 / 无覆盖整库的批次)即留空 ``None``,如实标注、
当场打印原因,不阻断跑批。
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from python_backend.corpus.schema import CorpusDocument, content_hash, load_corpus
from python_backend.db.models import CorpusBatch
from python_backend.db.session import SessionFactory


@dataclass(frozen=True)
class CorpusAnchor:
    """一次跑批的语料版本锚:指纹(必得)+ 批次附记(尽力)。"""

    fingerprint: str
    batch_id: str | None
    note: str = ""  # 附记缺席时的原因(打印用;快照只落 fingerprint / batch_id)


def corpus_fingerprint(documents: list[CorpusDocument]) -> str:
    """全部文档内容哈希的聚合指纹:按 doc_id 排序后逐行 ``<doc_id>:<内容哈希>`` 再取 sha256。

    排序在前 ⇒ 与文件顺序、摄入顺序无关——同一份语料真源永远同一指纹(重算确定性)。
    """
    lines = sorted(f"{document.doc_id}:{content_hash(document)}" for document in documents)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def latest_full_batch(rows: Sequence[tuple[str, str]], *, doc_ids: set[str]) -> str | None:
    """台账行 ``(batch_id, doc_id)``(按 ``ingested_at`` 升序)→ 覆盖整库的最近批次 id。

    「覆盖整库」= 该批次的 doc_id 集合 ⊇ 真源全部文档(部分摄入批次不冒充整库版本);
    「最近」取行序(调用方按 ingested_at 排序、batch_id 是 uuid4 无序——不能拿 id 比大小)。
    """
    if not doc_ids:
        return None
    batches: dict[str, set[str]] = {}
    for batch_id, doc_id in rows:
        batches.setdefault(batch_id, set()).add(doc_id)
    for batch_id in reversed(list(batches)):
        if doc_ids <= batches[batch_id]:
            return batch_id
    return None


async def read_ledger_rows() -> list[tuple[str, str]]:
    """读摄入台账(settings 指向的库),按 ``ingested_at`` 升序返回 ``(batch_id, doc_id)`` 行。"""
    statement = select(CorpusBatch.batch_id, CorpusBatch.doc_id).order_by(CorpusBatch.ingested_at, CorpusBatch.id)
    async with SessionFactory() as session:
        result = await session.execute(statement)
        return [(row.batch_id, row.doc_id) for row in result]


async def corpus_anchor(
    paths: list[Path], *, read_rows: Callable[[], Awaitable[list[tuple[str, str]]]] = read_ledger_rows
) -> CorpusAnchor:
    """语料文件 → 版本锚:指纹离线重算(必得),批次附记尽力而为(读不到留空 + 记原因)。"""
    documents = load_corpus(paths)
    fingerprint = corpus_fingerprint(documents)
    try:
        rows = await read_rows()
    except Exception as error:  # 台账读不到不该阻断跑批:指纹是主锚,附记缺席如实标注
        return CorpusAnchor(fingerprint=fingerprint, batch_id=None, note=f"摄入台账读不到({error})——批次附记留空")
    batch_id = latest_full_batch(rows, doc_ids={document.doc_id for document in documents})
    note = "" if batch_id else "台账无「覆盖整库」批次(部分摄入?)——批次附记留空"
    return CorpusAnchor(fingerprint=fingerprint, batch_id=batch_id, note=note)
