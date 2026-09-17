"""缝 1(票 #59):judge 结果解析与判据组装——固定输入 → 断言 0/1 + comment 的收敛行为(含坏输出)。

judge 是**跨厂**模型(中转站),输出不合约即 ``JudgeError``(带原文片段),绝不把解析不到的
判定猜成通过。「只发朴素字段」在 HTTP 层核实(``httpx2.MockTransport`` 假传输,不触真网)。
"""

from __future__ import annotations

import json
from dataclasses import replace

import httpx2
import pytest
from anthropic import Anthropic

from python_backend.evals.judge import (
    AnthropicJudge,
    JudgeConfig,
    JudgeError,
    JudgeRequest,
    api_base_url,
    parse_judgment,
    render_prompt,
)

CONFIG = JudgeConfig(model="claude-opus-5", api_url="https://relay.example.com/v1/messages", api_key="sk-relay")


def _request(criteria: tuple[str, ...] = ("判据甲", "判据乙")) -> JudgeRequest:
    return JudgeRequest(
        scenario_id="coffee-maker-us",
        slice_no=1,
        input="分析一下便携咖啡机在美国市场的选品机会",
        answer="美国市场咖啡机需求上行 [1]",
        citations=(
            {
                "number": 1,
                "doc_id": "usitc-digital-trade",
                "title": "全球数字贸易",
                "source": "USITC",
                "chunks": [{"id": "usitc-digital-trade#3", "content": "2016 年全球可穿戴市场增长 20%,达 $16.2B。"}],
            },
        ),
        criteria=criteria,
    )


def test_parse_judgment_reads_one_line_per_criterion() -> None:
    """标准形:一条判据一行「序号|0 或 1|理由」;行序、理由原样收。"""
    scores = parse_judgment("1|1|给了明确的评分等级\n2|0|结论没有引用支撑\n", expected=2)

    assert [(score.index, score.passed, score.comment) for score in scores] == [
        (1, True, "给了明确的评分等级"),
        (2, False, "结论没有引用支撑"),
    ]


def test_parse_judgment_sorts_and_tolerates_shape_noise() -> None:
    """输出噪声(乱序行、空行、整段围栏、理由里带竖线)→ 仍按序号收齐、按序返回。"""
    text = "```\n2|0|没有依据 | 理由里带竖线\n\n1|1|通过了\n```"

    scores = parse_judgment(text, expected=2)

    assert [(score.index, score.passed, score.comment) for score in scores] == [
        (1, True, "通过了"),
        (2, False, "没有依据 | 理由里带竖线"),
    ]


@pytest.mark.parametrize(
    ("text", "expected", "reason"),
    [
        ("1|1|甲", 2, "漏判一条"),
        ("1|1|甲\n2|1|乙\n3|1|丙", 2, "多判一条"),
        ("1|1|甲\n1|0|乙", 1, "同一序号两行"),
        ("1|maybe|甲\n2|0|乙", 2, "判定值不是 0/1"),
        ("一句话结论:都很好,没有问题", 2, "整段散文、无一行成形"),
        ("1|1\n2|0|乙", 2, "缺分隔段"),
        ("1|1|\n2|0|乙", 2, "理由空"),
        ("甲|1|没有序号\n乙|0|乙", 2, "序号不是整数"),
    ],
)
def test_parse_judgment_rejects_bad_output(text: str, expected: int, reason: str) -> None:
    """坏输出有明确报错姿态:带模型原文片段(含换行的以转义形呈现),便于对着原文查(不吞、不猜)。"""
    with pytest.raises(JudgeError) as error:
        parse_judgment(text, expected=expected)

    assert "judge" in str(error.value)  # 报错出自 judge 面(不吞成别的错)
    assert text.strip().splitlines()[0] in str(error.value)  # 原文片段在报错里(定位用)


def test_render_prompt_carries_question_answer_citations_and_criteria() -> None:
    """判据请求 → 提示词:任务指令 / 产出 / 引用条目(编号 → 出处 + **切块正文**)/ 评分标准 / 输出格式。"""
    prompt = render_prompt(_request())

    assert "分析一下便携咖啡机在美国市场的选品机会" in prompt
    assert "美国市场咖啡机需求上行 [1]" in prompt
    assert "[1] 全球数字贸易 — USITC" in prompt
    assert "usitc-digital-trade#3:2016 年全球可穿戴市场增长 20%,达 $16.2B。" in prompt
    assert "1. 判据甲" in prompt and "2. 判据乙" in prompt
    assert "<标准序号>|<0 或 1>|<一句话理由>" in prompt  # 输出格式约束写进提示词(不靠参数)


def test_render_prompt_carries_chunk_text_so_citations_are_verifiable() -> None:
    """#64 A1:判「引用是否支撑答案」要能**核验**,故切块正文须进提示词。

    只给 doc 级标题 + 切块编号时,judge 无从知道该编号里写了什么;配合「宁可判失败」的硬指令,
    面对无法核验的论断只能记 0——2026-09-16 批次批 5 条失败即由此误判(载荷里本就有原文)。
    """
    request = JudgeRequest(
        scenario_id="cs-workbench-stock-zh",
        slice_no=1,
        input="桌面收纳架 深空黑款现在还有货吗?大概什么时候能发货?",
        answer="加急专线东南亚 3-5 个工作日、欧美 5-7 个工作日,运费加收 $45-$90 [1]",
        citations=(
            {
                "number": 1,
                "doc_id": "faq-logistics",
                "title": "下单后多久发货?",  # doc 级标题只取首块:加急那条的标题根本不出现
                "source": "自造 FAQ 语料库",
                "chunks": [
                    {"id": "faq-logistics#1", "content": "Q: 下单后多久发货?\nA: 现货商品在付款后 48 小时内发出。"},
                    {
                        "id": "faq-logistics#13",
                        "content": (
                            "Q: 可以加急配送吗?\nA: 支持加急:东南亚 3-5 个工作日、欧美 5-7 个工作日,运费 $45-$90。"
                        ),
                    },
                ],
            },
        ),
        criteria=("结论性陈述有查证证据支撑",),
    )

    prompt = render_prompt(request)

    assert "faq-logistics#13:Q: 可以加急配送吗?" in prompt  # 判定所依据的正文在场
    assert "$45-$90" in prompt


def test_render_prompt_truncates_long_chunk_with_explicit_mark() -> None:
    """超长切块截断——但**显式标注**(不静默截):judge 须知道看到的不全。"""
    body = "正文" * 500  # 远超单块上限
    request = JudgeRequest(
        scenario_id="s",
        slice_no=1,
        input="问",
        answer="答 [1]",
        citations=(
            {"number": 1, "doc_id": "d", "title": "t", "source": "s", "chunks": [{"id": "d#0", "content": body}]},
        ),
        criteria=("判据甲",),
    )

    prompt = render_prompt(request)

    assert "d#0:正文正文" in prompt
    assert f"…(截断,全文 {len(body)} 字)" in prompt
    assert body not in prompt  # 确实截了


def test_render_prompt_caps_total_citation_budget_explicitly() -> None:
    """引用块总预算:命中多时后段切块只给标识 + 从略标注(不静默丢,也不无界膨胀提示词)。"""
    chunks = [{"id": f"d#{index}", "content": "填" * 600} for index in range(30)]
    request = JudgeRequest(
        scenario_id="s",
        slice_no=1,
        input="问",
        answer="答 [1]",
        citations=({"number": 1, "doc_id": "d", "title": "t", "source": "s", "chunks": chunks},),
        criteria=("判据甲",),
    )

    prompt = render_prompt(request)

    assert "(正文从略——引用块已达总预算)" in prompt
    assert "d#29:(正文从略——引用块已达总预算)" in prompt  # 末块如实标从略,不是消失
    assert len(prompt) < 12000  # 预算罩得住:30 块 x 600 字不会被整段塞进来


def test_render_prompt_marks_chunk_without_content() -> None:
    """载荷没带正文(旧快照 / 非检索条目)如实标注——不假装有证据,也不当作有正文。"""
    request = JudgeRequest(
        scenario_id="s",
        slice_no=1,
        input="问",
        answer="答 [1]",
        citations=({"number": 1, "doc_id": "d", "title": "t", "source": "s", "chunks": [{"id": "d#0"}]},),
        criteria=("判据甲",),
    )

    assert "d#0:(无正文)" in render_prompt(request)


def test_render_prompt_states_action_results_are_not_evidence() -> None:
    """#64 A1:动作类陈述(工单/草稿/审批)非「查证证据」——边界写进判定要求。

    依据:``cs-task-returns-zh#3#3`` 判词把「已登记升级工单(工单号 2)」当作无据论断;那是
    **真实工具产出**而非编造。工具结果不进本载荷(收口取措辞收窄,不取载荷扩容)。
    """
    prompt = render_prompt(_request())

    assert "动作执行结果" in prompt
    assert "不是「查证证据」" in prompt


def test_render_prompt_marks_absent_citations() -> None:
    """无引用载荷如实标注(不假装有依据):judge 判「结论有检索依据」该失败就失败。"""
    request = JudgeRequest(
        scenario_id="coffee-maker-us", slice_no=1, input="问题", answer="答", citations=(), criteria=("判据甲",)
    )

    assert "(无——该产出没有引用条目)" in render_prompt(request)


def _workbench_request(*, products_truncated: bool = False) -> JudgeRequest:
    """工作台线一条判据请求(带查证证据块与记录条目引用;#67 的判分材料形状)。"""
    return JudgeRequest(
        scenario_id="cs-workbench-stock-zh",
        slice_no=1,
        input="桌面收纳架 深空黑款现在还有货吗?大概什么时候能发货?",
        answer="该款当前库存仅剩 2 件 [2];发货时效见另一条 [1]。",
        citations=(
            {
                "number": 1,
                "kind": "corpus",
                "doc_id": "faq-logistics",
                "title": "下单后多久发货?",
                "source": "自造 FAQ 语料库",
                "published_at": None,
                "chunks": [{"id": "faq-logistics#1", "content": "现货商品在付款后 48 小时内发出。"}],
            },
            {
                "number": 2,
                "kind": "product",
                "title": "桌面收纳架 深空黑款",
                "source": "商品库(系统查询结果)",
                "record": {"id": "product:82", "content": "SKU SYN-HM-081 · 状态 draft · 库存 2"},
            },
        ),
        criteria=("涉及具体商品(库存/价格/在售状态)时逐款给出查证结论,不编造;未查到如实说明",),
        evidence={
            "faq_hits": [{"id": "faq-logistics#1", "payload": {"content": "Q: 下单后多久发货?"}}],
            "order": None,
            "order_id": None,
            "products": [
                {
                    "id": 82,
                    "sku": "SYN-HM-081",
                    "title": "桌面收纳架 深空黑款",
                    "price": "129.00",
                    "currency": "CNY",
                    "status": "draft",
                    "stock": 2,
                }
            ],
            "products_truncated": products_truncated,
        },
    )


def test_render_prompt_carries_evidence_block_and_record_citation() -> None:
    """#67:查证证据块进判分材料(商品/订单事实可核),记录条目在引用条目段可对。

    依据(2026-09-17 实评):``cs-workbench-stock-zh#1#2/#1#3`` 判 0,判词「库存结论缺失引用标记 /
    无对应商品查证证据」——而净库里该款库存确有 2 件,缺的是**判分材料这一半**(快照不带证据块)。
    """
    prompt = render_prompt(_workbench_request())

    assert "【查证证据(系统查询结果:商品/订单;FAQ 命中含未被引用的)】" in prompt
    assert "商品查证(按买家消息文本查库)命中 1 款:" in prompt
    assert "SYN-HM-081" in prompt and "库存 2" in prompt  # 判据②要核的在售事实在场
    assert "订单查证:买家消息未提供订单号" in prompt
    assert "FAQ 命中 1 条:" in prompt and "faq-logistics#1:Q: 下单后多久发货?" in prompt  # 未被引的命中也给
    assert "product:82:SKU SYN-HM-081 · 状态 draft · 库存 2" in prompt  # 记录条目(引用条目段)
    assert "(命中已截断" not in prompt


def test_render_prompt_marks_truncated_product_evidence() -> None:
    """证据截断**显式标注**(不静默截):judge 须知道自己看到的不是全部(#67)。"""
    prompt = render_prompt(_workbench_request(products_truncated=True))

    assert "(命中已截断:以上不是全部——更多商品未进入本材料)" in prompt


def test_render_prompt_keeps_evidence_absence_and_emptiness_apart() -> None:
    """缺席(None,该切片没有这份载荷)不渲染该段;**有块但三类皆空**(查过、没查到)如实呈现。"""
    assert "【查证证据(系统查询结果" not in render_prompt(_request())

    empty = replace(
        _request(),
        evidence={"faq_hits": [], "order": None, "order_id": 1042, "products": [], "products_truncated": False},
    )
    prompt = render_prompt(empty)

    assert "【查证证据(系统查询结果:商品/订单;FAQ 命中含未被引用的)】" in prompt
    assert "FAQ 命中:无" in prompt
    assert "商品查证:无命中" in prompt
    assert "订单查证:订单 #1042 未查到(如实标注,未编造)" in prompt


def _taskline_request(*, pruned: bool = False) -> JudgeRequest:
    """任务线一条判据请求(带系统记录查证块;#69 的形状——订单线 list_orders 读回的事实)。"""
    entry: dict = {
        "tool": "list_orders",
        "params": {"status": None},
        "result": [{"reference": "SYN-ORD-00001", "status": "delivered", "product": {"sku": "SYN-PL-001"}}],
    }
    if pruned:
        entry["truncated"] = True
        entry["rows_total"] = 2000
    return JudgeRequest(
        scenario_id="cs-task-returns-zh",
        slice_no=3,
        input="你们的退货政策是什么?我买的商品有点问题想退。",
        answer="比如 SYN-ORD-00001(智能插座 深空黑款)目前是 delivered,可以登记退货流转。",
        citations=(),  # 该片没有引用条目(判 0 的那条实录正是「零引用」)
        criteria=("结论性陈述有查证证据(FAQ/订单/商品)支撑:引用标记与查证证据对应,无凭空论断",),
        evidence={"lookups": [entry]},
    )


def test_render_prompt_carries_taskline_record_lookup() -> None:
    """#69:任务线的系统记录查证(**查回的值**,不是「调用记录」)进材料;标题按形状分派、不混用。"""
    prompt = render_prompt(_taskline_request())

    assert "【查证证据(系统记录查询结果:订单/商品)】" in prompt
    assert '工具 list_orders 查证(参数 {"status": null}):' in prompt
    assert '"reference": "SYN-ORD-00001"' in prompt and '"status": "delivered"' in prompt
    assert "【查证证据(系统查询结果:商品/订单;FAQ 命中含未被引用的)】" not in prompt
    assert "该查询共" not in prompt  # 未裁剪即不摆裁剪标注


def test_render_prompt_marks_pruned_lookup_rows() -> None:
    """裁剪行数**显式标注**(不静默截):judge 须知道自己看到的不是全部(#69,同 #64 A1 的口径)。"""
    prompt = render_prompt(_taskline_request(pruned=True))

    assert "(该查询共 2000 行,本材料给出其中 1 行:产出提及的记录优先,其余取头部)" in prompt


def test_render_prompt_scopes_product_claims_out_of_citation_marker_rule() -> None:
    """#67 口径句:商品/订单系系统查询结果——有材料支撑即算有据,不因缺编号判失败;

    反过来,证据段里没有的该类事实照判凭空(「无凭空论断」这一半不许被这句话放掉)。
    """
    prompt = render_prompt(_request())

    assert "商品/订单是系统查询结果" in prompt
    assert "不因其未标引用编号而判失败" in prompt
    assert "凭空论断" in prompt


def test_render_prompt_swaps_production_block_for_plan_request() -> None:
    """规划切片面(票 #61):判分对象是整份切片计划 → 产出段换成【切片计划】,不带答案与引用条目区。"""
    request = JudgeRequest(
        scenario_id="plan-category-trend-zh",
        slice_no=0,
        input="分析一下便携咖啡机在美国市场的选品机会",
        answer="",
        citations=(),
        criteria=("依赖声明与执行先序一致", "切片划分合理(≤5 片)", "领域路由正确"),
        plan="(共 2 片)\n切片 1:业务域 product_research | 说明:检索美国市场情报 | 依赖:无 | 审批点:无",
    )

    prompt = render_prompt(request)

    assert "【切片计划(Manager 的规划产出:切片划分 + 依赖声明)】" in prompt
    assert "切片 1:业务域 product_research | 说明:检索美国市场情报 | 依赖:无 | 审批点:无" in prompt
    assert "【产出(切片 0)】" not in prompt  # 计划面不摆一个空产出段
    assert "【该产出的引用条目(编号 → 出处 + 切块正文 / 系统查询记录)】" not in prompt
    assert "1. 依赖声明与执行先序一致" in prompt and "3. 领域路由正确" in prompt


def test_anthropic_judge_sends_naive_messages_request() -> None:
    """真实客户端 + 假传输:打到 ``<基址>/v1/messages``,体里只有朴素字段(messages + max_tokens)。"""
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"content": [{"type": "text", "text": "1|1|有据可依"}]})

    judge = AnthropicJudge(CONFIG, client=_client(handler))

    scores = judge.judge(_request(criteria=("结论有检索依据",)))

    assert [score.passed for score in scores] == [True]
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == "https://relay.example.com/v1/messages"
    assert request.headers["x-api-key"] == "sk-relay"
    body = json.loads(request.content)
    assert set(body) == {"model", "max_tokens", "messages"}  # 朴素字段:不透传原生参数(ADR-0008)
    assert body["model"] == "claude-opus-5"
    assert body["messages"][0]["role"] == "user"
    assert "结论有检索依据" in body["messages"][0]["content"]


def test_anthropic_judge_wraps_transport_failure() -> None:
    """网络 / 中转站故障(非 2xx)→ 收敛成 ``JudgeError``(编排层只认这一种错)。"""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(502, json={"error": "bad gateway"})

    with pytest.raises(JudgeError, match="judge 调用失败"):
        AnthropicJudge(CONFIG, client=_client(handler)).judge(_request())


def test_anthropic_judge_rejects_response_without_text_block() -> None:
    """中转给回怪响应(无文本块)→ ``JudgeError``,不当作「没有失败」。"""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"content": []})

    with pytest.raises(JudgeError, match="没有文本块"):
        AnthropicJudge(CONFIG, client=_client(handler)).judge(_request())


def test_api_base_url_strips_messages_and_version_segments() -> None:
    """URL 按 messages 端点给(.env.example 的写法)→ 去末段与版本段(SDK 自拼 ``/v1/messages``)。"""
    assert api_base_url("https://relay.example.com/v1/messages") == "https://relay.example.com"
    assert api_base_url("https://relay.example.com/v1/messages/") == "https://relay.example.com"
    assert api_base_url("https://relay.example.com/v1") == "https://relay.example.com"
    assert api_base_url("https://relay.example.com") == "https://relay.example.com"


def _client(handler) -> Anthropic:
    return Anthropic(
        api_key=CONFIG.api_key,
        base_url=api_base_url(CONFIG.api_url),
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
