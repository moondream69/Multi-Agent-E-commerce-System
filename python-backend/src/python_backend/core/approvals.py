"""审批批次域(术语表:切片内高危写动作按类型打包的审批批次,批内同进同退)。

图节点依赖 ApprovalBatchStore 协议(注入测试内存实现 / 生产 PG 实现),
与增量 2 的 LlmClient/Planner 协议注入同一风格。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ApprovalBatchRecord:
    """落库后的审批批次记录(status 六态见 db.models.ApprovalStatus)。"""

    batch_id: str
    thread_id: str
    slice_no: int
    action_type: str
    actions: list[dict]
    status: str
    mode: str
    comment: str | None = None
    result: dict | None = None
    run_output: dict | None = None


class BatchAlreadyDecidedError(ValueError):
    """批次已决定且与本次决定冲突,重复决定被明确拒绝(spec #6 D5 幂等)。

    同决定重复(durable 重放)幂等返回,不抛。
    """


def initial_status(mode: str) -> str:
    """批次初始状态:approval → pending;shadow → shadow(只记录不阻塞)。"""
    return "shadow" if mode == "shadow" else "pending"


def decided_status(decision: str) -> str:
    """决定 → 批次终态。"""
    return "approved" if decision == "approve" else "rejected"


# 三层风险分类(宪章:免审=draft 内部编辑;审批=一切对外状态变更;禁做=不暴露)。
# 增量 4(spec #7):auto 层扩展为全部已暴露的只读/内部安全工具动作;
# 审批层 6 动作语义不变(增量 3 钉死);未知动作默认 forbidden(禁做工具不存在)。
_AUTO_ACTIONS = frozenset(
    {
        # 免审写:draft 内部编辑
        "draft.edit",
        "draft.create",
        # 只读/纯函数工具(选品)
        "trend_query",
        "competitor_analysis",
        "scoring",
        "generate_report",
        # 只读/纯函数工具(订单)
        "list_orders",
        "check_inventory",
        "detect_anomalies",
        "list_approvals",
        # 只读/内部安全工具(客服)
        "faq_search",
        "order_lookup",
        "translate",
        "sentiment_analysis",
        "manage_template",
        "escalate_ticket",
        "generate_draft",
    }
)
_APPROVAL_ACTIONS = frozenset(
    {
        "product.publish",
        "product.unpublish",
        "product.update_price",
        "product.delete",
        "order.transition",
        "order.cancel",
    }
)


def classify_action(action: str) -> str:
    """三层风险分类:auto(免审)/ approval(进护栏)/ forbidden(禁做)。

    未知动作默认 forbidden——约束前移到 LLM 看见工具之前(B7:禁做工具不存在)。
    """
    if action in _AUTO_ACTIONS:
        return "auto"
    if action in _APPROVAL_ACTIONS:
        return "approval"
    return "forbidden"


class ApprovalBatchStore(Protocol):
    """审批批次存储协议。

    create_batch 须幂等:durable 恢复时 Pregel 重放节点,同 batch_id 重复创建应返回既有记录。
    decide_batch 同决定幂等返回(重放安全),已决定且决定冲突抛 BatchAlreadyDecidedError。
    """

    async def create_batch(
        self,
        *,
        batch_id: str,
        thread_id: str,
        slice_no: int,
        action_type: str,
        actions: list[dict],
        mode: str,
        run_output: dict | None = None,
    ) -> ApprovalBatchRecord: ...

    async def decide_batch(self, *, batch_id: str, decision: str, comment: str | None = None) -> None: ...

    async def list_pending(self, thread_id: str) -> list[ApprovalBatchRecord]: ...

    async def list_by_slice(self, thread_id: str, slice_no: int) -> list[ApprovalBatchRecord]:
        """某线程某切片的全部批次(spec #7:durable 重放的子图缓存,存在即跳过子图重跑)。"""
        ...

    async def get_batch(self, *, batch_id: str) -> ApprovalBatchRecord | None: ...

    async def list_open(self) -> list[ApprovalBatchRecord]:
        """全量未决批次(pending + shadow):审批中心数据源(spec #7)。"""
        ...


# 自然消息决定意图关键词(spec #6 D4:增量 3 用确定性规则,LLM 解析留增量 4/5)
_TERMINATE_WORDS = ("终止", "取消", "算了", "作罢")
_REJECT_WORDS = ("拒绝", "驳回", "不同意", "不行", "不通过")
_APPROVE_WORDS = ("批准", "同意", "通过", "可以", "ok")


def parse_decision_intent(text: str) -> str | None:
    """自然消息决定意图判定:terminate > reject > approve 优先级,无命中返回 None。

    「不同意」含「同意」——必须先匹配拒绝词,否则误判为 approve。
    """
    lowered = text.lower()
    if any(word in lowered for word in _TERMINATE_WORDS):
        return "terminate"
    if any(word in lowered for word in _REJECT_WORDS):
        return "reject"
    if any(word in lowered for word in _APPROVE_WORDS):
        return "approve"
    return None
