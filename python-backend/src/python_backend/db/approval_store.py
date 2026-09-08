"""审批批次 PG 存储(ApprovalBatchStore 生产实现,spec #6 D1)。

幂等语义(durable 重放防护):create_batch 按 batch_id 唯一约束 upsert,重放返回既有记录;
decide_batch 同决定幂等返回(重放安全),已决定且决定冲突抛 BatchAlreadyDecidedError。
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from python_backend.core.approvals import (
    ApprovalBatchRecord,
    ApprovalBatchStore,
    BatchAlreadyDecidedError,
    decided_status,
    initial_status,
)
from python_backend.db.models import ApprovalBatch
from python_backend.db.session import SessionFactory


def _to_record(row: ApprovalBatch) -> ApprovalBatchRecord:
    return ApprovalBatchRecord(
        batch_id=row.batch_id,
        thread_id=row.thread_id,
        slice_no=row.slice_no,
        action_type=row.action_type,
        actions=row.actions,
        status=row.status,
        mode=row.mode,
        comment=row.comment,
    )


class PostgresApprovalBatchStore(ApprovalBatchStore):
    """审批批次 PG 实现(approval_batches 表,模型已随 Alembic 0001 就位)。"""

    async def create_batch(
        self,
        *,
        batch_id: str,
        thread_id: str,
        slice_no: int,
        action_type: str,
        actions: list[dict],
        mode: str,
    ) -> ApprovalBatchRecord:
        status = initial_status(mode)
        async with SessionFactory() as session:
            statement = (
                pg_insert(ApprovalBatch)
                .values(
                    batch_id=batch_id,
                    thread_id=thread_id,
                    slice_no=slice_no,
                    action_type=action_type,
                    actions=actions,
                    status=status,
                    mode=mode,
                    requested_by="manager",
                )
                .on_conflict_do_nothing(index_elements=[ApprovalBatch.batch_id])
            )
            await session.execute(statement)
            await session.commit()
            row = (await session.execute(select(ApprovalBatch).where(ApprovalBatch.batch_id == batch_id))).scalar_one()
            return _to_record(row)

    async def decide_batch(self, *, batch_id: str, decision: str, comment: str | None = None) -> None:
        new_status = decided_status(decision)
        async with SessionFactory() as session:
            result = await session.execute(
                update(ApprovalBatch)
                .where(ApprovalBatch.batch_id == batch_id, ApprovalBatch.status == "pending")
                .values(status=new_status, comment=comment, decided_at=func.now())
            )
            await session.commit()
            if result.rowcount == 0:  # ty: ignore[unresolved-attribute]
                row = (
                    await session.execute(select(ApprovalBatch.status).where(ApprovalBatch.batch_id == batch_id))
                ).scalar_one_or_none()
                if row == new_status:
                    return  # 同决定幂等返回:durable 重放可能重复调用,已成功决定不得演变为失败
                current = row if row is not None else "不存在"
                raise BatchAlreadyDecidedError(f"批次 {batch_id} 已决定({current})")

    async def list_pending(self, thread_id: str) -> list[ApprovalBatchRecord]:
        async with SessionFactory() as session:
            rows = (
                await session.execute(
                    select(ApprovalBatch).where(ApprovalBatch.thread_id == thread_id, ApprovalBatch.status == "pending")
                )
            ).scalars()
            return [_to_record(row) for row in rows]
