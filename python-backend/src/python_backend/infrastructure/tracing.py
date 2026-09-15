"""Langfuse 层级观测(spec #7 B14):thread 为根 trace,规划/切片/审批决定为层级 observation。

LlmService 的 generation 埋点(LlmTracer)经 Langfuse contextvar 自动挂入当前 trace 内,
无需改 LlmService;未配置 Langfuse(host 留空)时全部 no-op。
审批决定事件携带 batch_id 落 observation,与 approval_batches 审计行互链。
任务 trace 的 trace_id 由 thread_id 确定性派生(spec #8 遗留:resume 续写同一 trace,不再分裂)。
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
