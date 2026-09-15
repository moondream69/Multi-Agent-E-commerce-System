"""Langfuse 层级观测(spec #7 B14):thread 为根 trace,规划/切片/审批决定为层级 observation。

LlmService 的 generation 埋点(LlmTracer)经 Langfuse contextvar 自动挂入当前 trace 内,
无需改 LlmService;未配置 Langfuse(host 留空)时全部 no-op。
审批决定事件携带 batch_id 落 observation,与 approval_batches 审计行互链。
任务 trace 的 trace_id 由 thread_id 确定性派生(spec #8 遗留:resume 续写同一 trace,不再分裂)。
评测根 trace(评测线,无任务轨迹的起草工作台线)的 id 与名同法派生于此——两处派生同址,
免得「谁依赖谁」的错觉(run 建它、score 挂它,两个编排都不是对方的依赖)。
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any, Protocol
from uuid import NAMESPACE_OID, uuid5

from python_backend.settings import get_settings


def task_trace_id(thread_id: str) -> str:
    """任务 trace 的确定性 trace_id:同一 thread 的所有阶段(创建/resume/重放)合并同一 trace。

    Langfuse 的 trace_id 契约是 **32 位小写十六进制**(langfuse 4.x 的 ``_is_valid_trace_id``;
    非契约值会被丢弃并告警),故取 ``uuid5(NAMESPACE_OID, thread_id)`` 的 hex 形式——确定性、
    无状态、无查库;可读标记留在 observation 名(``task:<thread_id>``)上。
    """
    return uuid5(NAMESPACE_OID, thread_id).hex


def eval_root_trace_name(scenario_id: str) -> str:
    """评测根 trace 的**可读标记**(trace 名 = 该名字;id 是它的确定性派生)。

    跑批器建 trace(``evals/projection.create_eval_trace``)与回评读分数两处共用——名与 id 拆开,
    改名不悄悄改 id(两处须恒等,见 ``eval_root_trace_id``)。
    """
    return f"eval:{scenario_id}"


def eval_root_trace_id(scenario_id: str) -> str:
    """评测根 trace 的确定性 trace_id(与 ``task_trace_id`` 同法:uuid5 的 hex)。

    起草工作台线没有任务轨迹(``POST /api/drafting`` 是同步端点),分数得挂在跑批器自建的
    评测根 trace 上(ADR-0008)——id 由场景 id 派生,建与挂两处无状态对齐。
    """
    return uuid5(NAMESPACE_OID, eval_root_trace_name(scenario_id)).hex


class TaskTracer(Protocol):
    """任务观测协议:图节点与 REST 层依赖此协议(测试注入记录实现)。"""

    def trace(self, thread_id: str) -> AbstractContextManager: ...
    def span(self, name: str, input: dict | None = None) -> AbstractContextManager: ...
    def record_event(self, name: str, output: dict) -> None: ...


class NullTaskTracer:
    """Langfuse 未配置时的 no-op(与 LlmService 的 NullTracer 同语义)。"""

    def trace(self, thread_id: str) -> AbstractContextManager:
        return nullcontext()

    def span(self, name: str, input: dict | None = None) -> AbstractContextManager:
        return nullcontext()

    def record_event(self, name: str, output: dict) -> None:
        return None


class LangfuseTaskTracer:
    """Langfuse 自托管观测:start_as_current_observation 返回上下文管理器(进出即嵌套/结束,
    并设为当前观察,嵌套 observation 与 LlmService 的 generation 埋点自动挂入)。

    client 可注入(测试假实现);未配置 Langfuse(host 留空)且未注入时 no-op。
    """

    def __init__(self, client: Any | None = None) -> None:
        settings = get_settings()
        self._enabled = client is not None or bool(
            settings.langfuse_host and settings.langfuse_public_key and settings.langfuse_secret_key
        )
        self._client = client
        if self._enabled and self._client is None:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
        if self._enabled:
            assert self._client is not None  # enabled 必有 client(注入或初始化)

    def _require_client(self) -> Any:
        assert self._client is not None  # enabled 必有 client(注入或初始化)
        return self._client

    def trace(self, thread_id: str) -> AbstractContextManager:
        if not self._enabled:
            return nullcontext()
        # trace_id 由 thread_id 确定性派生:create/resume/重放各阶段 observation 合并进同一任务 trace。
        # langfuse 4.x 的关联键是 trace_context(langfuse.types.TraceContext),不是旧版的 trace_id 直参。
        return self._require_client().start_as_current_observation(
            name=f"task:{thread_id}",
            as_type="span",
            trace_context={"trace_id": task_trace_id(thread_id)},
        )

    def span(self, name: str, input: dict | None = None) -> AbstractContextManager:
        if not self._enabled:
            return nullcontext()
        return self._require_client().start_as_current_observation(name=name, as_type="span", input=input)

    def record_event(self, name: str, output: dict) -> None:
        if not self._enabled:
            return
        # 事件以 span 型 observation 记录(当前上下文中嵌套,即挂入所在 trace/span)
        observation = self._require_client().start_observation(name=name, as_type="span", output=output)
        observation.end()
