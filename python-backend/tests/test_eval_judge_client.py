"""缝 1(票 #59):judge 结果解析与判据组装——固定输入 → 断言 0/1 + comment 的收敛行为(含坏输出)。

judge 是**跨厂**模型(中转站),输出不合约即 ``JudgeError``(带原文片段),绝不把解析不到的
判定猜成通过。「只发朴素字段」在 HTTP 层核实(``httpx2.MockTransport`` 假传输,不触真网)。
"""

from __future__ import annotations

import json

import httpx2
import pytest
from anthropic import Anthropic

from python_backend.evals.judge import (
    AnthropicJudge,
    JudgeConfig,
    JudgeError,
    JudgeRequest,
    api_base_url,
    eval_root_trace_id,
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
                "chunks": [{"id": "usitc-digital-trade#3"}],
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
    """判据请求 → 提示词:任务指令 / 产出 / 引用条目(编号 → 出处)/ 评分标准 / 输出格式。"""
    prompt = render_prompt(_request())

    assert "分析一下便携咖啡机在美国市场的选品机会" in prompt
    assert "美国市场咖啡机需求上行 [1]" in prompt
    assert "[1] 全球数字贸易 — USITC;切块:usitc-digital-trade#3" in prompt
    assert "1. 判据甲" in prompt and "2. 判据乙" in prompt
    assert "<标准序号>|<0 或 1>|<一句话理由>" in prompt  # 输出格式约束写进提示词(不靠参数)


def test_render_prompt_marks_absent_citations() -> None:
    """无引用载荷如实标注(不假装有依据):judge 判「结论有检索依据」该失败就失败。"""
    request = JudgeRequest(
        scenario_id="coffee-maker-us", slice_no=1, input="问题", answer="答", citations=(), criteria=("判据甲",)
    )

    assert "(无——该产出没有引用条目)" in render_prompt(request)


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


def test_eval_root_trace_id_is_hex32_and_deterministic() -> None:
    """工作台线的评测根 trace id:32 位小写十六进制(langfuse 契约)、按场景确定性派生。"""
    trace_id = eval_root_trace_id("coffee-maker-us")

    assert len(trace_id) == 32 and trace_id == trace_id.lower()
    assert all(char in "0123456789abcdef" for char in trace_id)
    assert trace_id == eval_root_trace_id("coffee-maker-us")
    assert trace_id != eval_root_trace_id("smart-band-us")


def _client(handler) -> Anthropic:
    return Anthropic(
        api_key=CONFIG.api_key,
        base_url=api_base_url(CONFIG.api_url),
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
