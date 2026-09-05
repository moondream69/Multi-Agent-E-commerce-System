"""extract_output_text 纯单测(与前端 outputToContent 逐项镜像,优先级顺序即契约)。"""

from __future__ import annotations

import json

from python_backend.core.output_text import extract_output_text


def test_report_takes_priority() -> None:
    assert extract_output_text({"report": "报告正文", "result": "x"}) == "报告正文"


def test_result_fallback() -> None:
    assert extract_output_text({"result": "完成"}) == "完成"
    # 空串不算命中,滑到下一分支
    assert extract_output_text({"result": "", "message": "m"}) == "m"


def test_reply_fallback() -> None:
    assert extract_output_text({"reply": "回复内容"}) == "回复内容"


def test_alert_with_message() -> None:
    assert extract_output_text({"alert": True, "message": "库存告警"}) == "库存告警"


def test_alert_without_message_falls_back_to_json() -> None:
    dumped = extract_output_text({"alert": True})
    assert json.loads(dumped) == {"alert": True}


def test_alert_none_still_matches_in_semantics() -> None:
    # 对应前端 output.alert !== undefined:存在即命中
    assert extract_output_text({"alert": None, "message": "m"}) == "m"


def test_non_string_field_falls_through() -> None:
    assert extract_output_text({"report": {"a": 1}, "message": "m"}) == "m"


def test_unknown_keys_fallback_to_json() -> None:
    assert extract_output_text({"foo": 1}) == json.dumps({"foo": 1}, ensure_ascii=False, indent=2)


def test_empty_dict() -> None:
    assert extract_output_text({}) == "{}"
