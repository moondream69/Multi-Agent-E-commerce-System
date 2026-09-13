"""引用小点(issue #51 / ADR-0007 C 段):答案标记 → 切块级引用条目 + 文本归一化。

标记两式:①切块标识(`[faq-returns#6]`,客服 Agent 线——一次切片可多次检索,序号跨调用歧义);
②序号(`[1]`,起草线——证据块在提示词里按序编号,故仅该线 `allow_ordinals=True`)。
**序号式须显式放开**:客服线的答案里出现 `[2]`(枚举或幻觉)若被静默锚到命中列表第 2 条,
幻觉就洗成了合法引用(#45 的机械防伪引对它无效——它确实在命中集内)。

同一文档(**切块标识的「#」前缀**)合并为同一编号:按文本内首次出现排 1、2、3…,
标记归一化为 `[n]` 供前端渲染上标;解析不到的标记原样保留(不吞、不编——防伪引判据要看得见它)。
无命中即无引用。
"""

from __future__ import annotations

import re

# 标记:方括号内为切块标识或序号(单 token,不含空白与嵌套括号)
_CITATION_MARKER = re.compile(r"\[([^\[\]\s]+)\]")


def document_id(chunk_id: str) -> str:
    """切块标识 → 文档标识(`<文档标识>#<序号>` 的「#」前缀;无「#」即自身)。"""
    return chunk_id.split("#", 1)[0]


def retrieval_hits(tool_result: object) -> list[dict]:
    """工具结果里的检索命中(只有检索类工具才有 `hits` 形状,查单/查商品没有)。

    #51 的引用只建在检索命中上——无检索依据的产出(评分/翻译/查单)自然落空。
    """
    if isinstance(tool_result, dict):
        hits = tool_result.get("hits")
        if isinstance(hits, list):
            return [hit for hit in hits if isinstance(hit, dict)]
    return []


def _chunk_entry(hit: dict) -> dict:
    """命中 → 引用里的切块条目(原文 + 切块级溯源)。"""
    payload = hit.get("payload") or {}
    return {
        "id": hit["id"],
        "score": hit["score"],
        "section": payload.get("section", ""),
        "chunk_index": payload.get("chunk_index"),
        "content": payload.get("content", ""),
    }


def build_citations(text: str, hits: list[dict], *, allow_ordinals: bool = False) -> tuple[str, list[dict]]:
    """答案文本 + 本轮检索命中 → (归一化文本, 引用条目列表)。

    引用条目 = 文档级一条(doc_id/title/source/published_at + chunks[]),编号即列表序,
    前端按 `[编号]` 渲染上标、点开显示每条被引切块的原文与完整溯源。
    `allow_ordinals` 只由把证据按序编号并教给模型的调用方(起草线)开启。
    """
    by_id = {hit["id"]: hit for hit in hits}
    numbers: dict[str, int] = {}  # 文档标识 → 编号(同文档复用)
    citations: list[dict] = []

    def resolve(token: str) -> dict | None:
        if token in by_id:
            return by_id[token]
        if allow_ordinals and token.isdigit() and 1 <= int(token) <= len(hits):
            return hits[int(token) - 1]
        return None  # 解析不到:原样保留标记

    def replace(match: re.Match[str]) -> str:
        hit = resolve(match.group(1))
        if hit is None:
            return match.group(0)
        doc_id = document_id(hit["id"])
        number = numbers.get(doc_id)
        if number is None:
            number = numbers[doc_id] = len(citations) + 1
            payload = hit.get("payload") or {}
            citations.append(
                {
                    "number": number,
                    "doc_id": doc_id,
                    "title": payload.get("title", ""),
                    "source": payload.get("source", ""),
                    "published_at": payload.get("published_at"),
                    "chunks": [],
                }
            )
        entry = citations[number - 1]
        if all(chunk["id"] != hit["id"] for chunk in entry["chunks"]):
            entry["chunks"].append(_chunk_entry(hit))
        return f"[{number}]"

    return _CITATION_MARKER.sub(replace, text), citations
