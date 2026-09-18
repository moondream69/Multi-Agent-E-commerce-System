"""判分材料的【系统契约】段(issue #75 A):状态机 + 工具能力,**由系统代码派生**。

依据(2026-09-17 全量批 `cs-task-returns-zh#3#3` 判 0):订单片如实陈述了系统契约事实
(delivered→returned 合法、pending/confirmed 只能取消、没有「创建退货单」工具、无通知模板能力)
——**全部为真**(``VALID_TRANSITIONS`` 与 ``ORDER_TOOLS`` 即其出处),但判分材料的四段
(任务指令 / 产出 / 引用条目 / 查证证据)**没有一段能承载它们**,judge 按材料口径判「凭空论断」
是正确执行。状态流转类陈述是**已知判分抖动键**(2026-09-18 交接已留档,本批再次触发)。

修法 = 材料补一段**代码派生**的契约(非 LLM 生成、非快照载荷):订单状态机取
``agents.executor.VALID_TRANSITIONS``(全系统单一事实源),工具清单取
``agents.order_management.tools.ORDER_TOOLS`` 与 ``core.approvals.classify_action``
(免审 / 审批由注册表裁决,与运行时同一判定)。

**触发面收口**(#75 A 的验收句):只对 ``order_management`` 切片渲染——选品 / 规划 / 工作台
(``agent="drafting"``)/ 客服线的判分材料**零变化**(口径同 #69 的收口面:不收就不落)。
"""

from __future__ import annotations

from python_backend.agents.executor import VALID_TRANSITIONS, action_of
from python_backend.agents.order_management.tools import ORDER_TOOLS
from python_backend.core.approvals import classify_action

# 渲染契约段的业务域(收口面写死:新增域须论证「该域确有代码派生的契约事实」,不是顺手扩大)
CONTRACT_AGENTS = ("order_management",)

# 风险分类 → 材料里的中文标注(与前端审批中心同一套语义:免审 = 调用即生效)
_RISK_LABELS = {"auto": "免审", "approval": "审批", "forbidden": "禁做"}


def system_contract(agent: str) -> str:
    """业务域 → 【系统契约】段正文(非收口域返回空串,判分材料据此不渲染该段)。

    工具能力给**全集**:「没有某类工具」「某动作须审批」这类陈述只有对着全集才可核——
    清单本身即否定式陈述的载体(judge 不必猜系统有什么)。
    """
    if agent not in CONTRACT_AGENTS:
        return ""
    lines = ["订单状态流转(合法流转;cancelled / returned 为终态,不可再流转):"]
    lines.extend(
        f"  {status.value} → {'、'.join(target.value for target in targets) or '(无——终态)'}"
        for status, targets in VALID_TRANSITIONS.items()
    )
    lines.append("本域工具能力(免审 = 调用即生效;审批 = 调用后登记待人工批准、批准前不生效):")
    lines.extend(
        f"  {tool.name}({_RISK_LABELS.get(classify_action(action_of(tool.name)), '未知')}):{tool.description}"
        for tool in ORDER_TOOLS
    )
    lines.append("该清单即本域工具全集:「没有某类工具」「某动作须审批」这类陈述与本段一致即算有据。")
    return "\n".join(lines)
