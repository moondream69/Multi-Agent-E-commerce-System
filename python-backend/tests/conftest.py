"""测试共享夹具与工具:外部服务探测 + 图级单测共享脚手架(store/规划器/切片执行桩/LLM/向量)。"""

from __future__ import annotations

import math
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from python_backend.agents.executor import ApplyResult
from python_backend.core.approvals import (
    ApprovalBatchRecord,
    BatchAlreadyDecidedError,
    decided_status,
    initial_status,
)
from python_backend.core.planning import Slice, SlicePlan
from python_backend.infrastructure.llm import ToolCallResult
from python_backend.vector_repo.base import SearchHit, VectorRecord, VectorRepository


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
        run_output: dict | None = None,
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
            run_output=run_output,
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

    async def list_by_slice(self, thread_id: str, slice_no: int) -> list[ApprovalBatchRecord]:
        return [r for r in self._by_id.values() if r.thread_id == thread_id and r.slice_no == slice_no]

    async def get_batch(self, *, batch_id: str) -> ApprovalBatchRecord | None:
        return self._by_id.get(batch_id)

    async def list_open(self) -> list[ApprovalBatchRecord]:
        return [r for r in self._by_id.values() if r.status in ("pending", "shadow")]


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


def slice_agent(
    executed: list[int] | None = None,
    *,
    actions: list[dict] | None = None,
    answer: str | None = None,
) -> Callable[[Slice], Awaitable[dict]]:
    """切片执行桩(spec #7):记录执行序号,返回占位结果;可脚本化收集的审批动作。"""
    executed = executed if executed is not None else []

    async def run(slice_: Slice) -> dict:
        executed.append(slice_.no)
        result: dict = {"agent": slice_.agent, "description": slice_.description, "executed": True}
        if actions is not None:
            result["actions"] = actions
        if answer is not None:
            result["answer"] = answer
        return result

    return run


class FakeApply:
    """假 apply(spec #7 接缝):记录 (batch_id, actions),按需返回冲突结果。"""

    def __init__(self, *, outcomes: dict[str, ApplyResult] | None = None) -> None:
        self._outcomes = outcomes or {}
        self.calls: list[tuple[str, list[dict]]] = []

    async def __call__(self, batch_id: str, actions: list[dict]) -> ApplyResult:
        self.calls.append((batch_id, actions))
        return self._outcomes.get(batch_id, ApplyResult(applied=True))


class RecordingEmitter:
    """记录事件发射器(spec #7 接缝):按序收集 (event, payload)。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))

    def names(self) -> list[str]:
        return [event for event, _payload in self.events]


class RecordingTaskTracer:
    """记录观测调用(spec #7 接缝):trace/span 顺序与事件载荷,不依赖 Langfuse SDK。"""

    def __init__(self) -> None:
        self.traces: list[str] = []
        self.spans: list[tuple[str, dict | None]] = []
        self.events: list[tuple[str, dict]] = []

    def trace(self, thread_id: str):
        from contextlib import nullcontext

        self.traces.append(thread_id)
        return nullcontext()

    def span(self, name: str, input: dict | None = None):
        from contextlib import nullcontext

        self.spans.append((name, input))
        return nullcontext()

    def record_event(self, name: str, output: dict) -> None:
        self.events.append((name, output))


# —— 增量 4(spec #7):子图与执行器共享夹具 ——


class FakeLlm:
    """按序列返回 complete / 工具轮结果;记录调用轨迹。

    complete_with_tools 按调用序返回脚本化工具轮(ToolCallResult);
    complete 按序返回文本,未配置响应时显式报错(便于测试定位意外调用)。
    """

    def __init__(
        self,
        responses: list[str | Exception] | None = None,
        tool_rounds: list[ToolCallResult | Exception] | None = None,
    ) -> None:
        self._responses = responses or []
        self._tool_rounds = tool_rounds or []
        self.calls: list[dict] = []

    async def complete(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        json_mode: bool = False,
    ) -> str:
        self.calls.append({"method": "complete", "messages": messages, "json_mode": json_mode})
        if not self._responses:
            raise AssertionError("FakeLlm.complete 未配置响应")
        response = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> ToolCallResult:
        index = len([c for c in self.calls if c["method"] == "complete_with_tools"])
        self.calls.append({"method": "complete_with_tools", "messages": messages, "tools": tools})
        if not self._tool_rounds:
            return ToolCallResult(content="", tool_calls=[])
        response = self._tool_rounds[min(index, len(self._tool_rounds) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


class FakeEmbedding:
    """确定性向量(仅测接线,不测语义):按文本长度生成同值向量。"""

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float((len(text) % 7) + 1)] * self._dim for text in texts]


class InMemoryVectorRepository(VectorRepository):
    """向量仓库内存实现(契约参考实现同款语义):余弦相似度排序,filter 最简形状。"""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, VectorRecord]] = {}

    async def upsert(self, collection: str, records: list[VectorRecord]) -> None:
        self._store.setdefault(collection, {})
        for record in records:
            self._store[collection][record.id] = record

    async def search(
        self, collection: str, vector: list[float], *, top_k: int, filter: str | None = None
    ) -> list[SearchHit]:
        hits = []
        for record in self._store.get(collection, {}).values():
            if filter and not _matches(record, filter):
                continue
            hits.append(SearchHit(id=record.id, score=_cosine(vector, record.vector), payload=record.payload))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]

    async def delete(self, collection: str, ids: list[str]) -> None:
        for id_ in ids:
            self._store.get(collection, {}).pop(id_, None)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def _matches(record: VectorRecord, filter: str) -> bool:
    field, value = filter.split(" == ")
    return record.payload.get(field) == value.strip('"')


class FakeExecutor:
    """假执行器:execute 记录并返回脚本结果;capture 记录并返回脚本快照。"""

    def __init__(
        self,
        results: dict[str, dict] | None = None,
        snapshots: dict[str, dict] | None = None,
    ) -> None:
        self._results = results or {}
        self._snapshots = snapshots or {}
        self.executed: list[tuple[str, dict]] = []
        self.captured: list[tuple[str, dict]] = []

    async def execute(self, action: str, params: dict) -> dict:
        self.executed.append((action, params))
        return self._results.get(action, {"ok": True})

    async def capture(self, action: str, params: dict) -> dict:
        self.captured.append((action, params))
        return self._snapshots.get(action, {"exists": True, "status": "draft"})
