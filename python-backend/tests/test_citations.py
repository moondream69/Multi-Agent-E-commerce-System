"""引用解析用例(issue #51):两种标记式、同文档合并、解析不到原样保留、无命中无引用。"""

from __future__ import annotations

from python_backend.core.citations import build_citations, document_id


def hit(
    chunk_id: str,
    *,
    score: float = 0.9,
    title: str = "退款多久到账?退到哪里?",
    source: str = "自造 FAQ 语料库",
    published_at: str = "2026-09-14",
    section: str = "退货退款",
    chunk_index: int = 6,
    content: str = "Q: 退款多久到账?\nA: 仓库验收后 1-3 个工作日发起,退回原支付渠道。",
) -> dict:
    """一条检索命中(形状 = ToolExecutor 检索结果:`{id, score, payload}`)。"""
    return {
        "id": chunk_id,
        "score": score,
        "payload": {
            "doc_id": document_id(chunk_id),
            "title": title,
            "source": source,
            "published_at": published_at,
            "section": section,
            "chunk_index": chunk_index,
            "content": content,
        },
    }


def test_ordinal_markers_are_numbered_by_first_appearance() -> None:
    """序号标记(起草线,须显式放开):编号按文本内首次出现排序,文本归一化为 [1]/[2]。"""
    hits = [
        hit("faq-returns#0", title="能退货吗?"),
        hit("faq-payment#3", title="退款退到哪里?", section="支付方式", chunk_index=3),
    ]
    text = "退款一般 1-3 个工作日到账[2]。能否退货见另一条[1]。"

    normalized, citations = build_citations(text, hits, allow_ordinals=True)

    assert normalized == "退款一般 1-3 个工作日到账[1]。能否退货见另一条[2]。"
    assert [entry["doc_id"] for entry in citations] == ["faq-payment", "faq-returns"]
    assert [entry["number"] for entry in citations] == [1, 2]


def test_ordinals_stay_verbatim_on_the_agent_line() -> None:
    """客服线(默认不放开序号):句中的 [2] 不得被静默锚到命中列表第 2 条(幻觉洗白)。"""
    hits = [hit("faq-returns#0", title="能退货吗?"), hit("faq-payment#3", title="退款退到哪里?")]

    normalized, citations = build_citations("退款 1-3 个工作日到账[2]。", hits)

    assert normalized == "退款 1-3 个工作日到账[2]。"  # 原样保留
    assert citations == []
    assert build_citations("退款到账[faq-payment#3]。", hits)[1][0]["doc_id"] == "faq-payment"  # 标识式照常


def test_chunk_id_markers_resolve_and_normalize() -> None:
    """切块标识标记(客服 Agent 线):按 id 解析,文本归一化为编号。"""
    hits = [hit("faq-returns#6", score=0.83)]

    normalized, citations = build_citations("仓库验收通过后 1-3 个工作日发起退款[faq-returns#6]。", hits)

    assert normalized == "仓库验收通过后 1-3 个工作日发起退款[1]。"
    assert citations[0]["doc_id"] == "faq-returns"
    assert [chunk["id"] for chunk in citations[0]["chunks"]] == ["faq-returns#6"]


def test_same_document_merges_into_one_number_with_all_cited_chunks() -> None:
    """同一文档多次引用合并为同一编号:两个切块归一条,两处标记都归一化为该编号。"""
    hits = [
        hit("faq-logistics#2", section="物流配送", chunk_index=2, content="海外仓 3-7 天。"),
        hit("faq-logistics#9", section="物流配送", chunk_index=9, content="旺季可能延长 2-3 天。"),
    ]

    normalized, citations = build_citations("一般 3-7 天[faq-logistics#2];旺季会慢一些[faq-logistics#9]。", hits)

    assert normalized == "一般 3-7 天[1];旺季会慢一些[1]。"
    assert len(citations) == 1
    assert citations[0]["number"] == 1
    assert [chunk["id"] for chunk in citations[0]["chunks"]] == ["faq-logistics#2", "faq-logistics#9"]
    assert citations[0]["chunks"][1]["section"] == "物流配送"


def test_unresolvable_markers_stay_verbatim() -> None:
    """解析不到的标记原样保留(越界序号 / 不存在的切块标识):不吞不编,机械防伪引看得见。"""
    hits = [hit("faq-returns#0")]

    normalized, citations = build_citations("据称如此[9],另有说法[faq-nowhere#1]。", hits)

    assert normalized == "据称如此[9],另有说法[faq-nowhere#1]。"
    assert citations == []


def test_no_hits_means_no_citations() -> None:
    """无检索命中(评分/翻译类产出):文本原样、引用为空。"""
    normalized, citations = build_citations("评分 88 分(A 级)。", [])

    assert normalized == "评分 88 分(A 级)。"
    assert citations == []


def test_citation_carries_chunk_text_and_full_provenance() -> None:
    """引用条目带被引切块原文与完整溯源(文档标识/标题/来源/日期/章节/切块序号)。"""
    hits = [
        hit(
            "usitc-global-digital-trade-1#583",
            score=0.5596,
            title="Global Digital Trade 1",
            source="U.S. International Trade Commission",
            published_at="2017-08-01",
            section="pp.150-151",
            chunk_index=583,
            content="Digital trade barriers vary by market.",
        )
    ]

    _normalized, citations = build_citations("跨境数据流动受限[usitc-global-digital-trade-1#583]。", hits)

    entry = citations[0]
    assert entry["title"] == "Global Digital Trade 1"
    assert entry["source"] == "U.S. International Trade Commission"
    assert entry["published_at"] == "2017-08-01"
    assert entry["chunks"][0] == {
        "id": "usitc-global-digital-trade-1#583",
        "score": 0.5596,
        "section": "pp.150-151",
        "chunk_index": 583,
        "content": "Digital trade barriers vary by market.",
    }


def test_document_id_splits_on_first_hash() -> None:
    """文档标识 = 首个「#」前缀;无「#」时即自身。"""
    assert document_id("faq-returns#6") == "faq-returns"
    assert document_id("plain-id") == "plain-id"
