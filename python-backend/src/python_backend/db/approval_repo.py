"""审批请求表读写(分级审批护栏的数据层)。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult

from python_backend.db.models import ApprovalRequest, ApprovalStatus
from python_backend.db.session import SessionLocal


def create_request(
    tool_name: str,
    params: dict[str, Any],
    agent_id: str | None,
    task_id: str | None,
    requested_by: str | None,
    mode: str,
) -> str:
    with SessionLocal() as session:
        row = ApprovalRequest(
            toolName=tool_name,
            params=params,
            agentId=agent_id,
            taskId=task_id,
            requestedBy=requested_by,
            mode=mode,
            status=ApprovalStatus.PENDING if mode == "approval" else ApprovalStatus.SHADOW,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return str(row.id)


def get_request(request_id: str) -> ApprovalRequest | None:
    with SessionLocal() as session:
        return session.get(ApprovalRequest, request_id)


def list_requests(status: str | None = None) -> list[ApprovalRequest]:
    with SessionLocal() as session:
        stmt = select(ApprovalRequest).order_by(ApprovalRequest.createdAt.desc())
        if status:
            stmt = stmt.where(ApprovalRequest.status == status)
        return list(session.scalars(stmt))


def decide_request(request_id: str, approve: bool, decided_by: str, comment: str | None) -> ApprovalRequest | None:
    with SessionLocal() as session:
        row = session.get(ApprovalRequest, request_id)
        if row is None or row.status != ApprovalStatus.PENDING:
            return None
        row.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        row.decidedBy = decided_by
        row.decidedAt = datetime.now(UTC)
        row.comment = comment
        session.commit()
        session.refresh(row)
        return row


def mark_executed(request_id: str, decided_by: str, result: dict[str, Any]) -> None:
    with SessionLocal() as session:
        session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == request_id)
            .values(
                status=ApprovalStatus.EXECUTED,
                decidedBy=decided_by,
                decidedAt=datetime.now(UTC),
                result=result,
            )
        )
        session.commit()


def mark_result(request_id: str, result: dict[str, Any]) -> None:
    with SessionLocal() as session:
        session.execute(update(ApprovalRequest).where(ApprovalRequest.id == request_id).values(result=result))
        session.commit()


def expire_one(request_id: str) -> None:
    """精确过期单个 pending 审批(进程内 TTL 耗尽兜底)。"""
    with SessionLocal() as session:
        session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == request_id, ApprovalRequest.status == ApprovalStatus.PENDING)
            .values(status=ApprovalStatus.EXPIRED)
        )
        session.commit()


def expire_stale(max_age_hours: float) -> int:
    """把超过存活期的 pending 置 expired(启动清扫 + TTL 兜底)。返回处理条数。"""
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    with SessionLocal() as session:
        stmt = (
            update(ApprovalRequest)
            .where(ApprovalRequest.status == ApprovalStatus.PENDING, ApprovalRequest.createdAt < cutoff)
            .values(status=ApprovalStatus.EXPIRED)
        )
        result = cast(CursorResult[Any], session.execute(stmt))
        session.commit()
        return result.rowcount or 0
