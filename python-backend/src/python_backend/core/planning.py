"""Manager 规划(术语表:切片计划 + 依赖声明 + fallback 安全网)。

宪章 ADR-0005:
- 规划输出 = 切片计划(≤5 步)+ 依赖声明(独立并行扇出/依赖串行)
- LLM 输出校验失败携错误重试,上限 2 次(共 3 次尝试)
- 步数超限强制终止,产出「未完成+原因」(B17)
- LLM 调用失败/超时 → 关键词规则确定性路由兜底(B13,旧 IntentParser 语义降级而来)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from python_backend.infrastructure.llm import LlmClient, LlmFailure, LlmService

# 三个业务域(宪章:选品/订单/客服三业务 Agent,不扩展)
AGENTS = ("product_research", "order_management", "customer_service")

# 规划步数上限(宪章:Manager 规划 5 步)
MAX_SLICES = 5

# LLM 输出校验重试上限(宪章:失败语义)
VALIDATION_RETRY_LIMIT = 2


class PlanningError(Exception):
    """切片计划校验失败(携人类可读原因,供重试提示与最终报告)。"""


@dataclass
class Slice:
    """切片:由人工断点分隔的执行段。depends_on 为依赖声明(引用切片 no)。"""

    no: int
    agent: str
    description: str
    depends_on: list[int] = field(default_factory=list)
    approval_points: list[str] = field(default_factory=list)


@dataclass
class SlicePlan:
    """Manager 规划产物:切片 + 依赖声明。"""

    slices: list[Slice]

    @classmethod
    def from_dict(cls, data: dict) -> SlicePlan:
        raw_slices = data.get("slices")
        if not isinstance(raw_slices, list) or not raw_slices:
            raise PlanningError("slices 缺失或为空")
        if len(raw_slices) > MAX_SLICES:
            raise PlanningError(f"切片数超上限({len(raw_slices)} > {MAX_SLICES})")

        slices: list[Slice] = []
        seen: set[int] = set()
        for raw in raw_slices:
            if not isinstance(raw, dict):
                raise PlanningError(f"切片格式非法:{raw!r}")
            no = raw.get("no")
            agent = raw.get("agent")
            description = raw.get("description")
            if not isinstance(no, int) or no <= 0:
                raise PlanningError(f"切片 no 非法:{no!r}")
            if no in seen:
                raise PlanningError(f"切片 no 重复:{no}")
            seen.add(no)
            if agent not in AGENTS:
                raise PlanningError(f"未知业务域:{agent!r}(合法值 {AGENTS})")
            if not description or not isinstance(description, str):
                raise PlanningError(f"切片 {no} 缺 description")
            depends_on = raw.get("depends_on") or []
            if not isinstance(depends_on, list) or not all(isinstance(d, int) for d in depends_on):
                raise PlanningError(f"切片 {no} depends_on 非法")
            if no in depends_on:
                raise PlanningError(f"切片 {no} 依赖自身")
            approval_points = raw.get("approval_points") or []
            slices.append(
                Slice(
                    no=no,
                    agent=agent,
                    description=description,
                    depends_on=depends_on,
                    approval_points=approval_points if isinstance(approval_points, list) else [],
                )
            )

        by_no = {s.no: s for s in slices}
        for s in slices:
            for dep in s.depends_on:
                if dep not in by_no:
                    raise PlanningError(f"切片 {s.no} 依赖不存在的切片 {dep}")

        cls._check_acyclic(slices, by_no)
        return cls(slices=slices)

    @staticmethod
    def _check_acyclic(slices: list[Slice], by_no: dict[int, Slice]) -> None:
        """拓扑排序检测环:有环 → 依赖声明非法。"""

        indegree = {s.no: len(s.depends_on) for s in slices}
        ready = [no for no, deg in indegree.items() if deg == 0]
        visited = 0
        while ready:
            no = ready.pop()
            visited += 1
            for s in slices:
                if no in s.depends_on:
                    indegree[s.no] -= 1
                    if indegree[s.no] == 0:
                        ready.append(s.no)
        if visited != len(slices):
            raise PlanningError("依赖声明存在环")

    def execution_order(self) -> list[list[int]]:
        """拓扑分层:每层内切片相互独立(可并行扇出),层间串行。"""
        layers: list[list[int]] = []
        remaining = {s.no: set(s.depends_on) for s in self.slices}
        while remaining:
            ready = sorted(no for no, deps in remaining.items() if not deps)
            if not ready:  # 不可达:from_dict 已保证无环,防御性兜底
                raise PlanningError("依赖声明存在环")
            layers.append(ready)
            for no in ready:
                del remaining[no]
            for deps in remaining.values():
                deps.difference_update(ready)
        return layers


@dataclass
class PlanFailed:
    """规划强制终止(未完成 + 原因),如实上抛给用户(宪章:永不静默吞错)。"""

    reason: str


# fallback 关键词表(旧 IntentParser 语义:命中数优先、平局按配置序、无命中兜底客服)。
# 注意旧教训:"客户抱怨物流太慢"——"物流"是订单域词,"客户/回复"是客服域词,命中数优先可正确路由客服。
_FALLBACK_PATTERNS: list[tuple[str, list[str]]] = [
    ("product_research", ["选品", "市场", "趋势", "竞品", "分析报告", "什么产品好卖"]),
    ("order_management", ["订单", "商品", "上架", "库存", "发货", "物流"]),
    ("customer_service", ["客户", "投诉", "FAQ", "翻译", "回复", "售后", "退货"]),
]

_SYSTEM_PROMPT = """你是多 Agent 电商系统的经理(Manager)。将用户需求规划为切片计划。

规则:
1. 切片数上限 {max_slices};切片按人工断点切分(高危写动作后需要人工审批处是天然断点)
2. 每个切片指定 agent:{agents} 之一
3. 领域路由:商品操作(上架/下架/改价/删除)与订单/库存管理 → order_management;
   选品调研/市场分析/报告 → product_research;买家消息处理/翻译/查证 → customer_service
4. 依赖声明:能并行的切片不互相依赖;有依赖的切片在 depends_on 中引用切片编号
5. 仅输出 JSON,格式:
{{"slices": [{{"no": 1, "agent": "...", "description": "...",
"depends_on": [], "approval_points": ["上架审批"]}}]}}
""".format(max_slices=MAX_SLICES, agents="/".join(AGENTS))


def fallback_route(text: str) -> SlicePlan:
    """关键词规则确定性路由(B13):产出单切片直达计划。"""
    best: tuple[int, str] | None = None
    for agent, keywords in _FALLBACK_PATTERNS:
        hits = sum(1 for kw in keywords if kw in text)
        if hits > 0 and (best is None or hits > best[0]):
            best = (hits, agent)
    agent = best[1] if best else "customer_service"
    return SlicePlan(slices=[Slice(no=1, agent=agent, description=text)])


class Planner(Protocol):
    """规划器协议:监督图依赖此协议,测试注入 StubPlanner。

    context = 会话记忆上下文(B16:短上下文+摘要),无历史时为 None。
    """

    async def plan(self, request: str, context: str | None = None) -> SlicePlan | PlanFailed: ...


class ManagerPlanner:
    """Manager 规划器:LLM 生成切片计划,校验失败携错误重试,LLM 失败 fallback。"""

    def __init__(self, llm: LlmClient | None = None) -> None:
        self._llm = llm or LlmService()

    async def plan(self, request: str, context: str | None = None) -> SlicePlan | PlanFailed:
        try:
            data, reason = await self._llm_plan_with_validation(request, context)
        except LlmFailure:  # 仅 LLM 调用失败/超时 → fallback 安全网(B13);编程错误继续上抛(永不静默吞错)
            return fallback_route(request)

        if data is None:
            return PlanFailed(f"规划失败:{reason}")
        return SlicePlan.from_dict(data)

    async def _llm_plan_with_validation(self, request: str, context: str | None) -> tuple[dict | None, str]:
        """LLM 生成 + 校验重试:上限 VALIDATION_RETRY_LIMIT 次,返回最终数据或失败原因。

        校验失败携错误重试:把失败原因作为用户消息反馈给 LLM,提高重试恢复率。
        会话记忆(B16):有历史上下文时注入用户消息前缀,Manager 携上下文规划。
        """
        last_reason = ""
        user_content = f"会话历史:\n{context}\n\n当前需求:{request}" if context else request
        messages: list[dict] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        for _attempt in range(VALIDATION_RETRY_LIMIT + 1):
            raw = await self._llm.complete(messages, json_mode=True)
            try:
                data = json.loads(raw)
                SlicePlan.from_dict(data)  # 校验先行:非法输出不落地
                return data, ""
            except (json.JSONDecodeError, PlanningError) as error:
                last_reason = str(error)
                messages.append(
                    {"role": "user", "content": f"上一次输出校验失败:{last_reason}。请只输出修正后的 JSON。"}
                )
        return None, last_reason
