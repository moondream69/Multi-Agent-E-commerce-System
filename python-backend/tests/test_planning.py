"""Manager 规划测试:SlicePlan 校验、拓扑分层、fallback 关键词路由(seam S6/S7/B13/B17)。"""

import json

import pytest

from python_backend.core.planning import (
    AGENTS,
    MAX_SLICES,
    ManagerPlanner,
    PlanFailed,
    PlanningError,
    SlicePlan,
)
from python_backend.infrastructure.llm import LlmFailure


def plan_dict(slices: list[dict]) -> dict:
    return {"slices": slices}


def slice_(no: int, agent: str = "order_management", **extra) -> dict:
    s = {"no": no, "agent": agent, "description": f"切片 {no}"}
    s.update(extra)
    return s


class FakeLlm:
    """按序列返回 complete 结果;可中途抛错(模拟 LLM 挂掉)。"""

    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = responses
        self.calls: list[dict] = []

    async def complete(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        json_mode: bool = False,
    ) -> str:
        self.calls.append({"messages": messages, "json_mode": json_mode})
        response = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


class TestSlicePlanValidation:
    async def test_valid_plan_parses(self) -> None:
        plan = SlicePlan.from_dict(plan_dict([slice_(1, depends_on=[]), slice_(2, depends_on=[1])]))
        assert len(plan.slices) == 2
        assert plan.slices[1].depends_on == [1]

    async def test_rejects_unknown_agent(self) -> None:
        with pytest.raises(PlanningError, match="业务域"):
            SlicePlan.from_dict(plan_dict([slice_(1, agent="finance")]))

    async def test_rejects_missing_dependency_reference(self) -> None:
        with pytest.raises(PlanningError, match="依赖"):
            SlicePlan.from_dict(plan_dict([slice_(1, depends_on=[9])]))

    async def test_rejects_self_dependency(self) -> None:
        with pytest.raises(PlanningError, match="依赖"):
            SlicePlan.from_dict(plan_dict([slice_(1, depends_on=[1])]))

    async def test_rejects_cycle(self) -> None:
        with pytest.raises(PlanningError, match="环"):
            SlicePlan.from_dict(plan_dict([slice_(1, depends_on=[2]), slice_(2, depends_on=[1])]))

    async def test_rejects_more_than_five_slices(self) -> None:
        """B17:Manager 规划步数上限 5。"""
        with pytest.raises(PlanningError, match="上限"):
            SlicePlan.from_dict(plan_dict([slice_(i) for i in range(1, 7)]))

    async def test_rejects_empty_plan(self) -> None:
        with pytest.raises(PlanningError):
            SlicePlan.from_dict(plan_dict([]))

    async def test_rejects_duplicate_slice_no(self) -> None:
        with pytest.raises(PlanningError):
            SlicePlan.from_dict(plan_dict([slice_(1), slice_(1)]))


class TestExecutionOrder:
    def test_independent_slices_are_parallel_layers(self) -> None:
        plan = SlicePlan.from_dict(plan_dict([slice_(1), slice_(2), slice_(3, depends_on=[1, 2])]))
        order = plan.execution_order()
        assert order == [[1, 2], [3]]  # 独立切片同层并行;依赖者后置

    def test_chain_is_serial(self) -> None:
        plan = SlicePlan.from_dict(plan_dict([slice_(1), slice_(2, depends_on=[1]), slice_(3, depends_on=[2])]))
        order = plan.execution_order()
        assert order == [[1], [2], [3]]

    def test_diamond_dependency(self) -> None:
        plan = SlicePlan.from_dict(
            plan_dict(
                [
                    slice_(1),
                    slice_(2),
                    slice_(3, depends_on=[1]),
                    slice_(4, depends_on=[1, 2]),
                    slice_(5, depends_on=[3, 4]),
                ]
            )
        )
        order = plan.execution_order()
        assert order == [[1, 2], [3, 4], [5]]


class TestPlanFailed:
    def test_plan_failed_carries_reason(self) -> None:
        failed = PlanFailed("规划步数超上限(6 > 5)")
        assert "上限" in failed.reason


def test_agents_are_the_three_business_domains() -> None:
    assert AGENTS == ("product_research", "order_management", "customer_service")


class TestManagerPlanner:
    async def test_plan_returns_validated_plan(self) -> None:
        llm = FakeLlm([json.dumps(plan_dict([slice_(1), slice_(2, depends_on=[1])]))])
        planner = ManagerPlanner(llm)
        result = await planner.plan("先查库存再补货")
        assert isinstance(result, SlicePlan)
        assert [s.no for s in result.slices] == [1, 2]
        assert llm.calls[-1]["json_mode"] is True

    async def test_retries_on_invalid_output_then_succeeds(self) -> None:
        """LLM 输出校验失败 → 携错误重试,上限 2 次(宪章:失败语义)。"""
        llm = FakeLlm(["这不是 JSON", json.dumps(plan_dict([slice_(1)]))])
        planner = ManagerPlanner(llm)
        result = await planner.plan("查库存")
        assert isinstance(result, SlicePlan)
        assert len(llm.calls) == 2
        assert "校验失败" in llm.calls[1]["messages"][-1]["content"]  # 重试携带失败原因反馈

    async def test_terminates_with_reason_when_always_over_limit(self) -> None:
        """B17:规划步数超限 → 校验重试 2 次后强制终止,产出「未完成+原因」。"""
        oversized = json.dumps(plan_dict([slice_(i) for i in range(1, MAX_SLICES + 2)]))
        llm = FakeLlm([oversized, oversized, oversized])
        planner = ManagerPlanner(llm)
        result = await planner.plan("上架 6 个商品")
        assert isinstance(result, PlanFailed)
        assert "上限" in result.reason
        assert len(llm.calls) == 3  # 1 次 + 2 次校验重试

    async def test_falls_back_to_keyword_routing_when_llm_fails(self) -> None:
        """B13:LLM 调用失败 → 关键词规则确定性路由兜底(单切片直达)。"""
        llm = FakeLlm([LlmFailure("DeepSeek 超时")])
        planner = ManagerPlanner(llm)
        result = await planner.plan("帮我查一下库存情况")
        assert isinstance(result, SlicePlan)
        assert len(result.slices) == 1
        assert result.slices[0].agent == "order_management"

    async def test_programming_errors_are_not_swallowed_by_fallback(self) -> None:
        """宪章:永不静默吞错——fallback 只承接 LlmFailure,编程错误继续上抛。"""
        llm = FakeLlm([RuntimeError("响应结构变化,编程错误")])
        planner = ManagerPlanner(llm)
        with pytest.raises(RuntimeError, match="编程错误"):
            await planner.plan("查库存")

    async def test_fallback_routes_customer_complaint_with_logistics_to_customer_service(self) -> None:
        """旧 IntentParser 教训回归守护:命中数优先("客户/回复"3 命中 vs "物流"1 命中)。"""
        result = await ManagerPlanner(FakeLlm([LlmFailure("挂")])).plan("客户抱怨物流太慢,帮我写个回复")
        assert isinstance(result, SlicePlan)
        assert result.slices[0].agent == "customer_service"

    async def test_fallback_defaults_to_customer_service(self) -> None:
        result = await ManagerPlanner(FakeLlm([LlmFailure("挂")])).plan("在吗")
        assert isinstance(result, SlicePlan)
        assert result.slices[0].agent == "customer_service"
