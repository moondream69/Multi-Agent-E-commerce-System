"""Workflow:Agent 可选声明的图级执行约束。

阶段(WorkflowPhase)依序推进:required_tools 为阶段内必调工具(历史 tool_calls
全部覆盖才进入下一阶段),allowed_tools 为可见工具白名单(None=全部可见),
can_answer 为无 tool_calls 输出时是否允许终止。

当前仅客服声明两阶段(先必调 sentiment_analysis 与 faq_search,再解锁全部
工具);未声明 workflow 的 Agent 行为完全不变。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowPhase:
    required_tools: frozenset[str]
    allowed_tools: frozenset[str] | None
    can_answer: bool


@dataclass(frozen=True)
class Workflow:
    phases: tuple[WorkflowPhase, ...]
