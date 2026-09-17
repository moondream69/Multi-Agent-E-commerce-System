"""引用小点(issue #51 / ADR-0007 C 段):答案标记 → 引用条目 + 文本归一化。

标记两式:①切块标识(`[faq-returns#6]`,客服 Agent 线——一次切片可多次检索,序号跨调用歧义);
②序号(`[1]`,起草线——证据块在提示词里按序编号,故仅该线 `allow_ordinals=True`)。
**序号式须显式放开**:客服线的答案里出现 `[2]`(枚举或幻觉)若被静默锚到命中列表第 2 条,
幻觉就洗成了合法引用(#45 的机械防伪引对它无效——它确实在命中集内)。

**引用源两类**(issue #67):语料命中(`{id, score, payload}`,切块级溯源)与**系统记录**
(`{kind, title, source, record}`——商品/订单查库结果;它不是语料切块,故条目给 `record`
而非 `chunks`)。起草线把两类证据**统一编号**(ref 贯穿 FAQ/订单/商品),故序号解析对
整份引用源清单生效——**ref 序与清单序必须同源**(`core/drafting.py` 的 `_numbered_sources`
是唯一编序点)。其余线只收语料命中,清单里没有记录,行为与 #51 原样一致。

同一文档(**切块标识的「#」前缀**;系统记录按 `record.id`)合并为同一编号:按文本内首次出现排
1、2、3…,标记归一化为 `[n]` 供前端渲染上标;解析不到的标记原样保留(不吞、不编——防伪引判据
要看得见它)。无命中即无引用。

机械防伪引(issue #57):`check_citations` 判**归一化之后**的文本——标记规则与 `build_citations`
同一条(单一解析点):能锚定的已变 `[n]`,不在载荷编号内的方括号 token 即疑似伪造(「原样保留」
正是为此)。判定二式 = 残留标记(带字符位置)/ 载荷条目与命中集不相交;两者皆无的产出判**不适用**,
不作通过计(口径与边界见 `evals/schema.py` 的决策记录;归一化的已知边界见 `check_citations`)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

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


def _is_record(source: dict) -> bool:
    """引用源是不是**系统记录**(商品/订单查库结果,#67):带 ``record`` 键即它,其余按语料命中。"""
    return "record" in source


def _source_key(source: dict) -> str:
    """合并键:语料命中按**文档标识**(同文档合并,原口径),系统记录按 ``record.id``(同一记录合并)。

    记录键加前缀:与文档标识同处一个映射,不能有撞面的可能。
    """
    if _is_record(source):
        return f"record:{source['record']['id']}"
    return document_id(source["id"])


def _source_entry(source: dict, number: int) -> dict:
    """引用源 → 引用条目:语料命中给切块级溯源(chunks 增量填),系统记录给查询结果条目。"""
    if _is_record(source):
        return {
            "number": number,
            "kind": source["kind"],
            "title": source.get("title", ""),
            "source": source.get("source", ""),
            "record": dict(source["record"]),
        }
    payload = source.get("payload") or {}
    return {
        "number": number,
        "kind": "corpus",  # 类别显式落载荷(#67):前端按它分流渲染,免「缺省值才知道含义」
        "doc_id": document_id(source["id"]),
        "title": payload.get("title", ""),
        "source": payload.get("source", ""),
        "published_at": payload.get("published_at"),
        "chunks": [],
    }


def build_citations(text: str, sources: list[dict], *, allow_ordinals: bool = False) -> tuple[str, list[dict]]:
    """答案文本 + 本轮引用源清单 → (归一化文本, 引用条目列表)。

    引用条目 = 一条一号(语料按文档合并、系统记录按记录合并),编号即列表序,前端按 `[编号]`
    渲染上标、点开显示被引内容与完整溯源。`allow_ordinals` 只由把证据按序编号并教给模型的调用方
    (起草线)开启——开启时序号按**整份清单**解析,故调用方的编序与清单序必须同源(#67)。
    """
    by_id = {source["id"]: source for source in sources if "id" in source}
    numbers: dict[str, int] = {}  # 合并键 → 编号(同文档/同记录复用)
    citations: list[dict] = []

    def resolve(token: str) -> dict | None:
        if token in by_id:
            return by_id[token]
        if allow_ordinals and token.isdigit() and 1 <= int(token) <= len(sources):
            return sources[int(token) - 1]
        return None  # 解析不到:原样保留标记

    def replace(match: re.Match[str]) -> str:
        source = resolve(match.group(1))
        if source is None:
            return match.group(0)
        key = _source_key(source)
        number = numbers.get(key)
        if number is None:
            number = numbers[key] = len(citations) + 1
            citations.append(_source_entry(source, number))
        if not _is_record(source):  # 记录条目的正文在 record 里,不留 chunks(不假装是语料切块)
            entry = citations[number - 1]
            if all(chunk["id"] != source["id"] for chunk in entry["chunks"]):
                entry["chunks"].append(_chunk_entry(source))
        return f"[{number}]"

    return _CITATION_MARKER.sub(replace, text), citations


ViolationKind = Literal["残留标记", "载荷条目与命中集不相交"]


@dataclass(frozen=True)
class CitationViolation:
    """一条机械防伪引违规:kind 取「残留标记」/「载荷条目与命中集不相交」;位置仅前式有。"""

    kind: ViolationKind
    detail: str
    position: int | None = None


@dataclass(frozen=True)
class CitationCheck:
    """机械防伪引判定:applicable=False(既无标记也无载荷)即本判据**不适用**,不作通过计。"""

    applicable: bool
    violations: tuple[CitationViolation, ...]


def check_citations(text: str, citations: list[dict] | None, *, hits: list[dict] | None = None) -> CitationCheck:
    """答案文本 + citations 载荷(+ 可选本轮命中集)→ 机械防伪引判定(零 LLM;ADR-0007 钦定判据,票 #55 T1)。

    标记解析规则与 `build_citations` 同一条(单一解析点):编号集**直接读载荷条目的 `number`
    字段**(归一化产物只可能是这些编号;不另行推导 1..N——编号契约变则校验随动)。产出文本里
    锚得上的标记已被归一化为 `[n]`,编号集之外的方括号 token 即解析不到的残留——疑似伪造编号,
    逐条列出并带字符位置。

    载荷条目与命中集不相交 = 引用条目的切块全不在本轮检索命中内(载荷与检索事实不符);
    须传入 hits 才可判(快照可能只带产出、不带本轮命中),`hits=None` 即跳过此类。
    **系统记录条目(商品/订单,#67)不参与此式**:它不是语料切块,本就不该出现在命中集里——
    拿它去比会造出假违规。

    applicable=False 仅当文本无标记且载荷为空:无检索产出的答案(评分/查单类)**判不适用**,
    不判通过——零对象的通过分会稀释跨场景指标(口径与边界见 `evals/schema.py` 的决策记录)。

    **已知边界**(不可判,非本函数的实现选择):归一化产物也是 `[n]`,与残留**同形**——非序号
    线上的裸数字残留(模型编的 `[2]`)若恰落在编号集内,逐 token 无从与合法编号区分,如实漏报;
    此类须产出另随带原始文本或残留清单才可判,属消费面(#55 T2/T3)的接口决定。非序号线上的
    越界序号、切块标识式残留,以及序号线(起草线)上的全部残留,均逐条列出。
    """
    payload = citations or []
    markers = list(_CITATION_MARKER.finditer(text))
    if not markers and not payload:
        return CitationCheck(applicable=False, violations=())
    numbers = {str(entry["number"]) for entry in payload}
    violations = [
        CitationViolation(
            kind="残留标记",
            detail=f"{match.group(0)} 解析不到引用条目(疑似伪造编号)",
            position=match.start(),
        )
        for match in markers
        if match.group(1) not in numbers
    ]
    if hits is not None:
        hit_ids = {hit["id"] for hit in hits}
        for entry in payload:
            if "record" in entry:  # 系统记录条目:非语料切块,不参与「与命中集相交」判定(#67)
                continue
            chunk_ids = [chunk["id"] for chunk in entry.get("chunks") or []]
            if not any(chunk_id in hit_ids for chunk_id in chunk_ids):
                violations.append(
                    CitationViolation(
                        kind="载荷条目与命中集不相交",
                        detail=f"引用条目 [{entry.get('number')}] {entry.get('doc_id')} 的切块均不在本轮检索命中内",
                    )
                )
    return CitationCheck(applicable=True, violations=tuple(violations))
