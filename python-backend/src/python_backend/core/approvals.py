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
# 真实工具挂接在增量 4,分类表先行建立并钉死语义(B7 在增量 3 的静态分类层验收)。
_AUTO_ACTIONS = frozenset({"draft.edit", "draft.create"})
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
    ) -> ApprovalBatchRecord: ...

    async def decide_batch(self, *, batch_id: str, decision: str, comment: str | None = None) -> None: ...

    async def list_pending(self, thread_id: str) -> list[ApprovalBatchRecord]: ...


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
