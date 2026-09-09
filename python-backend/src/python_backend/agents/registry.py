"""动作注册表(spec #8 遗留收敛):分类/capture/apply/前端标签一处维护。

每个动作一条 ActionSpec:动作标识 + 风险分类(免审/审批)+ 中文标签 + 处理函数。
classify_action(approvals)、执行器分发(executor)、审批中心标签渲染(GET /api/actions)
全部读本注册表;未知动作默认 forbidden(禁做工具不存在,B7)。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

Risk = Literal["auto", "approval"]


@dataclass
class ActionSpec:
    """单个动作的完整规格:风险分类 + 中文标签 + 处理函数(按风险选装)。"""

    action: str
    risk: Risk
    label: str
    execute: Callable[..., Awaitable[Any]] | None = None  # auto:直行处理(executor, params)
    capture: Callable[..., Awaitable[dict]] | None = None  # approval:现状快照(session, params)
    # approval:事务内执行(session, params, snapshot, fx_rate);返回效果描述 list[dict](spec #9 通知源)
    apply: Callable[..., Awaitable[list[dict] | None]] | None = None


class ActionRegistry:
    """动作注册表:register 一处声明,classify/get/metadata 三处读。"""

    def __init__(self) -> None:
        self._specs: dict[str, ActionSpec] = {}

    def register(
        self,
        action: str,
        *,
        risk: Risk,
        label: str,
        execute: Callable[..., Awaitable[Any]] | None = None,
        capture: Callable[..., Awaitable[dict]] | None = None,
        apply: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        if action in self._specs:
            raise ValueError(f"动作 {action} 重复注册")
        self._specs[action] = ActionSpec(
            action=action, risk=risk, label=label, execute=execute, capture=capture, apply=apply
        )

    def get(self, action: str) -> ActionSpec | None:
        return self._specs.get(action)

    def classify(self, action: str) -> str:
        """三层风险分类:auto / approval / forbidden(未知动作默认禁做)。"""
        spec = self._specs.get(action)
        return spec.risk if spec else "forbidden"

    def metadata(self) -> list[dict]:
        """动作元数据(GET /api/actions 数据源):前端标签渲染不再硬编码。"""
        return [
            {"action": spec.action, "risk": spec.risk, "label": spec.label}
            for spec in sorted(self._specs.values(), key=lambda s: s.action)
        ]


REGISTRY = ActionRegistry()
