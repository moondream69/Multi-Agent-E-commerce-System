"""Agent 输出 → 展示文本的提取逻辑(持久化与历史渲染共用)。

与前端 frontend/src/utils/output.ts 的 outputToContent 逐项镜像,
字段优先级必须保持一致,否则前后端渲染结果会漂移。
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["extract_output_text"]


def extract_output_text(output: dict[str, Any]) -> str:
    """按前端同优先级从 Agent 输出 dict 提取人类可读文本;无可提取字段时 JSON 兜底。"""
    report = output.get("report")
    if isinstance(report, str) and report:
        return report
    result = output.get("result")
    if isinstance(result, str) and result:
        return result
    reply = output.get("reply")
    if isinstance(reply, str) and reply:
        return reply
    if "alert" in output:  # 对应前端 output.alert !== undefined(存在即命中,含 null)
        message = output.get("message")
        if isinstance(message, str):  # 允许空串,与前端同语义
            return message
        return json.dumps(output, ensure_ascii=False, indent=2)
    message = output.get("message")
    if isinstance(message, str) and message:
        return message
    return json.dumps(output, ensure_ascii=False, indent=2)
