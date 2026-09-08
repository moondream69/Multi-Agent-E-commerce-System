"""测试共享夹具与工具:外部服务探测 + 图级单测共享脚手架(store/规划器/切片执行桩)。"""

from __future__ import annotations

import socket
from collections.abc import Callable
from urllib.parse import urlparse

from python_backend.core.approvals import (
    ApprovalBatchRecord,
    BatchAlreadyDecidedError,
    decided_status,
    initial_status,
)
from python_backend.core.planning import Slice, SlicePlan


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def milvus_reachable(uri: str, timeout: float = 2.0) -> bool:
    """Milvus 在线 TCP 快速探测:gRPC 连接失败的重试放大很慢,先探端口再构造客户端。"""
    parsed = urlparse(uri)
    return _tcp_reachable(parsed.hostname or "", parsed.port or 19530, timeout)


def postgres_reachable(database_url: str, timeout: float = 2.0) -> bool:
    """Postgres 在线 TCP 快速探测:离线时 integration 秒 skip(默认超时放大至分钟级)。"""
    parsed = urlparse(database_url.replace("postgresql+psycopg://", "postgresql://"))
    return _tcp_reachable(parsed.hostname or "", parsed.port or 5432, timeout)


class InMemoryApprovalBatchStore:
    """批次存储内存实现(图级单测共享接缝;PG 实现见 db/approval_store)。

    幂等语义与生产实现一致:create 幂等、decide 同决定幂等返回、冲突抛错。
    """

    def __init__(self) -> None:
        self.batches: list[ApprovalBatchRecord] = []
        self._by_id: dict[str, ApprovalBatchRecord] = {}

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
        existing = self._by_id.get(batch_id)
        if existing is not None:
            return existing  # 幂等:durable 重放会重复调用
        record = ApprovalBatchRecord(
            batch_id=batch_id,
            thread_id=thread_id,
            slice_no=slice_no,
            action_type=action_type,
            actions=actions,
            status=initial_status(mode),
            mode=mode,
        )
        self.batches.append(record)
        self._by_id[batch_id] = record
        return record

    async def decide_batch(self, *, batch_id: str, decision: str, comment: str | None = None) -> None:
        record = self._by_id[batch_id]
        new_status = decided_status(decision)
        if record.status != "pending":
            if record.status == new_status:
                return  # 同决定幂等返回(durable 重放)
            raise BatchAlreadyDecidedError(f"批次 {batch_id} 已决定({record.status})")
        record.status = new_status
        record.comment = comment

    async def list_pending(self, thread_id: str) -> list[ApprovalBatchRecord]:
        return [r for r in self._by_id.values() if r.thread_id == thread_id and r.status == "pending"]


class StubPlanner:
    """规划器桩:固定计划(默认单审批切片),捕获每次规划收到的请求文本。"""

    def __init__(self, plan: SlicePlan | None = None) -> None:
        self._plan = plan or SlicePlan(
            slices=[Slice(no=1, agent="order_management", description="上架商品", approval_points=["上架审批"])]
        )
        self.requests: list[str] = []

    async def plan(self, request: str) -> SlicePlan:
        self.requests.append(request)
        return self._plan


def slice_agent(executed: list[int] | None = None) -> Callable[[Slice], dict]:
    """切片执行桩:记录执行序号,返回占位结果。"""
    executed = executed if executed is not None else []

    def run(slice_: Slice) -> dict:
        executed.append(slice_.no)
        return {"agent": slice_.agent, "description": slice_.description, "executed": True}

    return run
