"""判分材料的【系统契约】段(#75 A):由代码派生的状态机与工具能力——纯离线。

依据(2026-09-17 全量批 ``cs-task-returns-zh#3#3``):订单片如实陈述「delivered→returned、pending/
confirmed 只能取消、没有创建退货单的工具、无通知模板能力」——**全是真的**(出处即
``VALID_TRANSITIONS`` 与 ``ORDER_TOOLS``),但判分材料四段(任务指令/产出/引用条目/查证证据)
没有一段能承载系统契约 ⇒ judge 按材料口径判「凭空论断」是正确执行。

触发面**收口在订单域**:选品/规划/工作台/客服线物料零变化(#75 A 的验收句,同 #69 的收口面口径)。
"""

from __future__ import annotations

from python_backend.evals.system_contract import CONTRACT_AGENTS, system_contract


def test_contract_carries_order_state_machine() -> None:
    """状态机取 ``VALID_TRANSITIONS``(全系统单一事实源):七态流转如实呈现,终态可辨。"""
    contract = system_contract("order_management")

    assert "订单状态流转" in contract
    assert "pending → confirmed、cancelled" in contract
    assert "delivered → returned" in contract
    assert "cancelled → (无——终态)" in contract
    assert "returned → (无——终态)" in contract


def test_contract_carries_tool_capabilities_with_risk_classes() -> None:
    """工具能力给**全集** + 免审/审批标注(与注册表同一判定)——否定式陈述(「没有某工具」)靠它可核。"""
    contract = system_contract("order_management")

    assert "本域工具能力" in contract
    assert "list_orders(免审):查询订单列表(只读)" in contract
    assert "order_transition(审批):订单状态流转(审批动作" in contract
    assert "order_cancel(审批):取消订单" in contract
    assert "该清单即本域工具全集" in contract


def test_contract_only_renders_for_order_domain() -> None:
    """收口面写死:非订单域返回空串 ⇒ 判分材料不渲染该段(选品/规划/工作台/客服线零变化)。"""
    assert CONTRACT_AGENTS == ("order_management",)
    for agent in ("product_research", "customer_service", "drafting", ""):
        assert system_contract(agent) == ""
