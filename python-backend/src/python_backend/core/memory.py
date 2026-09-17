"""会话记忆(spec #8 B16):短上下文(最近 N 轮)+ 超窗 LLM 摘要。

- SessionMemory 协议:get_context(规划上下文)/ record(对话落库),测试注入内存实现
- PostgresSessionMemory:conversations 表按 (user_id, session_id) 一行;
  messages 为对话列表,summary 为超窗摘要 JSON({text, summarized_at})
- 摘要策略:超过 SUMMARIZE_THRESHOLD 条时,把最旧的(总数-N)条压缩进 summary,保留最近 N 条;
  LLM 摘要失败(LlmFailure)记日志跳过——记忆是辅助路径,不阻断任务
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select

from python_backend.db.models import Conversation
from python_backend.db.session import SessionFactory
from python_backend.infrastructure.llm import (
    AGENT_MAX_TOKENS,
    LlmClient,
    LlmFailure,
    LlmService,
    complete_with_blank_retry,
)

logger = logging.getLogger(__name__)

# 最近 N 轮短上下文(B16 暂存值;实现期可调,偏差记录进 issue)
CONTEXT_MESSAGES = 6
# 超窗阈值:消息数超过即触发摘要,压缩到最近 N 条
SUMMARIZE_THRESHOLD = 12


class SessionMemory(Protocol):
    """会话记忆协议:监督图/REST 依赖此协议(测试注入内存实现)。"""

    async def get_context(self, session_id: str, user_id: int | None) -> str | None: ...

    async def record(
        self, session_id: str, user_id: int | None, *, role: str, content: str, task_id: str | None = None
    ) -> None: ...


class PostgresSessionMemory:
    """conversations 表实现:LLM 摘要经注入的 llm 客户端(测试假实现)。"""

    def __init__(self, llm: LlmClient | None = None) -> None:
        self._llm = llm or LlmService()

    async def _row(self, session_id: str, user_id: int | None) -> Conversation | None:
        if user_id is None:
            return None  # 未认证上下文不落库(非认证测试路径)
        async with SessionFactory() as session:
            return (
                await session.execute(
                    select(Conversation).where(Conversation.user_id == user_id, Conversation.session_id == session_id)
                )
            ).scalar_one_or_none()

    async def get_context(self, session_id: str, user_id: int | None) -> str | None:
        """组装规划上下文:「对话摘要(如有)+ 最近 N 轮」;无历史 → None。"""
        row = await self._row(session_id, user_id)
        if row is None or not row.messages:
            return None
        parts: list[str] = []
        if row.summary:
            parts.append(f"[对话摘要]\n{row.summary.get('text', '')}")
        recent = row.messages[-CONTEXT_MESSAGES:]
        parts.append(
            "\n".join(
                f"{'用户' if message.get('role') == 'user' else '助手'}: {message.get('content', '')}"
                for message in recent
            )
        )
        return "\n".join(parts)

    async def record(
        self, session_id: str, user_id: int | None, *, role: str, content: str, task_id: str | None = None
    ) -> None:
        """对话落库:追加消息;超窗触发 LLM 摘要压缩(失败跳过,不阻断)。"""
        if user_id is None or not content:
            return
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(UTC).isoformat(),
            "task_id": task_id,
        }
        needs_summary = False
        async with SessionFactory() as session, session.begin():
            row = (
                await session.execute(
                    select(Conversation).where(Conversation.user_id == user_id, Conversation.session_id == session_id)
                )
            ).scalar_one_or_none()
            if row is None:
                row = Conversation(
                    user_id=user_id,
                    session_id=session_id,
                    title=content[:20],  # A2 语义预埋:标题截断 20 字
                    messages=[],
                )
                session.add(row)
            row.messages = [*row.messages, message]
            needs_summary = len(row.messages) > SUMMARIZE_THRESHOLD
        if needs_summary:
            await self._summarize(session_id, user_id)  # 事务外摘要(LLM 网络不持事务)

    async def _summarize(self, session_id: str, user_id: int) -> None:
        """把最旧消息压缩进 summary,保留最近 CONTEXT_MESSAGES 条;LLM 失败记日志跳过。"""
        async with SessionFactory() as session:
            row = (
                await session.execute(
                    select(Conversation).where(Conversation.user_id == user_id, Conversation.session_id == session_id)
                )
            ).scalar_one_or_none()
            if row is None or len(row.messages) <= SUMMARIZE_THRESHOLD:
                return
            to_summarize = row.messages[:-CONTEXT_MESSAGES]
        history = "\n".join(f"{m.get('role')}: {m.get('content', '')}" for m in to_summarize)
        try:
            text = await complete_with_blank_retry(
                self._llm,
                [
                    {
                        "role": "system",
                        "content": "你是会话摘要器。把对话历史压缩为一段中文摘要(要点+结论,200 字内)。",
                    },
                    {"role": "user", "content": history},
                ],
                max_tokens=AGENT_MAX_TOKENS,  # 摘要正文短,但思考照样吃穿预算(原 400 曾饿空)——同档(#68)
            )
        except LlmFailure as error:
            logger.warning("会话摘要失败(跳过,不阻断任务): %s", error)
            return
        async with SessionFactory() as session, session.begin():
            row = (
                await session.execute(
                    select(Conversation).where(Conversation.user_id == user_id, Conversation.session_id == session_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.summary = {"text": text, "summarized_at": datetime.now(UTC).isoformat()}
            row.messages = row.messages[-CONTEXT_MESSAGES:]
