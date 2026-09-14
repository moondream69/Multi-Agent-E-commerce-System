"""引用解析用例(issue #51):两种标记式、同文档合并、解析不到原样保留、无命中无引用。

机械防伪引用例(issue #57)并列于此:校验是引用解析的下游消费,接缝同款(给文本与载荷 → 断言违规清单)。
"""

from __future__ import annotations

import pytest

from python_backend.core.citations import build_citations, check_citations, document_id


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


# --- 机械防伪引(issue #57):零 LLM 的引用合规校验 -----------------------------------


def test_check_passes_a_normal_answer() -> None:
    """正常答案零违规:解析到并锚定的标记不算违规(不误伤)。"""
    hits = [hit("faq-returns#6")]
    text, citations = build_citations("仓库验收后 1-3 个工作日发起退款[faq-returns#6]。", hits)

    report = check_citations(text, citations, hits=hits)

    assert report.applicable
    assert report.violations == ()


def test_check_passes_drafting_ordinals_after_normalization() -> None:
    """起草线产出(提示词给了证据 ref,序号式放开)归一化后同判——校验只看归一化文本。"""
    hits = [hit("faq-returns#0", title="能退货吗?"), hit("faq-payment#3", title="退款退到哪里?")]
    text, citations = build_citations("退款 1-3 个工作日到账[2]。能否退货见另一条[1]。", hits, allow_ordinals=True)

    report = check_citations(text, citations, hits=hits)

    assert report.applicable
    assert report.violations == ()


def test_check_lists_residual_markers_with_positions() -> None:
    """伪造答案被逐条列出:残留标记(越界序号 / 不存在的切块标识)带**位置**。"""
    hits = [hit("faq-returns#6")]
    text, citations = build_citations("据称如此[9],另有说法[faq-nowhere#1],再有一条[faq-returns#6]。", hits)

    report = check_citations(text, citations, hits=hits)

    assert report.applicable
    assert [violation.kind for violation in report.violations] == ["残留标记", "残留标记"]
    assert [violation.position for violation in report.violations] == [
        text.index("[9]"),
        text.index("[faq-nowhere#1]"),
    ]
    assert "[9]" in report.violations[0].detail
    assert "[faq-nowhere#1]" in report.violations[1].detail


def test_check_flags_payload_entries_disjoint_from_hits() -> None:
    """载荷条目与命中集不相交:引用条目的切块全不在本轮检索命中内(载荷与检索事实不符)。"""
    text, citations = build_citations("退款到账[faq-returns#6]。", [hit("faq-returns#6")])

    report = check_citations(text, citations, hits=[hit("faq-logistics#2")])

    assert report.applicable
    assert [violation.kind for violation in report.violations] == ["载荷条目与命中集不相交"]
    assert report.violations[0].position is None  # 载荷类违规没有文本位置
    assert "faq-returns" in report.violations[0].detail


def test_check_reports_both_kinds_on_mixed_forgery() -> None:
    """混合伪造:残留标记与不相交载荷同现时两类都报,不互相遮蔽。"""
    text, citations = build_citations("退款到账[faq-returns#6],另有说法[faq-nowhere#1]。", [hit("faq-returns#6")])

    report = check_citations(text, citations, hits=[hit("faq-logistics#2")])

    assert report.applicable
    assert [violation.kind for violation in report.violations] == ["残留标记", "载荷条目与命中集不相交"]


def test_check_known_gap_in_range_ordinals_are_indistinguishable() -> None:
    """**已知边界(不可判,非期望行为)**:非序号线上,编号集内的裸数字残留与本函数产物**同形**。

    末条 `[2]` 是模型在客服线上编的裸序号(build 原样保留、未锚定),却恰与合法编号 2 撞形——
    归一化产物也是 `[n]`,逐 token 无从区分,本函数如实漏报(详见 `check_citations` docstring)。
    补上信息流(产出随带原始文本或残留清单,#55 T2/T3 消费面决定)后此例应转红并改写。
    """
    hits = [hit("faq-payment#3", section="支付方式", chunk_index=3), hit("faq-returns#0", title="能退货吗?")]
    text, citations = build_citations("退款到账[faq-payment#3];退货见[faq-returns#0];另据称[2]。", hits)

    assert text.endswith("另据称[2]。")  # 残留原样保留在产出里(#51 语义),信号在,但同形不可判
    report = check_citations(text, citations, hits=hits)

    assert report.applicable
    assert report.violations == ()  # ← 已知漏报:三类残留里唯独这类同形不可判


def test_check_flags_markers_when_nothing_was_retrieved() -> None:
    """无命中却标了引用:载荷为空 → 每个标记都是残留(标了引用却没有引用条目可对)。"""
    text, citations = build_citations("退款 1-3 个工作日到账[1]。", [])

    report = check_citations(text, citations, hits=[])

    assert report.applicable  # 有标记即适用(「无标记且无载荷」才是不适用)
    assert [violation.kind for violation in report.violations] == ["残留标记"]
    assert report.violations[0].position == text.index("[1]")


def test_check_skips_payload_check_without_hits() -> None:
    """命中集可省:本轮命中集拿不到时只判标记(载荷无从对照,不凭空判违规)。"""
    text, citations = build_citations("退款到账[faq-returns#6]。", [hit("faq-returns#6")])

    report = check_citations(text, citations)

    assert report.applicable
    assert report.violations == ()


@pytest.mark.parametrize("citations", [[], None])
def test_check_is_not_applicable_without_any_citation_signal(citations: list[dict] | None) -> None:
    """无检索产出的答案(无标记、无载荷)判**不适用**,不判通过——零对象的通过分会稀释指标。"""
    report = check_citations("评分 88 分(A 级)。", citations)

    assert not report.applicable
    assert report.violations == ()
