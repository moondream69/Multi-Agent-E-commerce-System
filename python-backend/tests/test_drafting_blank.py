"""起草线空正文护栏(issue #65):思考吃穿预算 → 同预算原样重试一次,仍空才上抛。

``test_drafting.py`` 全模块经 requires_postgres 门控(#39 商品指代查证走真库),离线全套跳过;
本模块按 ``test_drafting_locales.py`` 同法只补需要的替身(llm + 商品指代;vector 置 None 即不查
FAQ、也不碰 embedding),让这条护栏进 CI 快速套件。
"""

from __future__ import annotations

import pytest

from python_backend.agents.base import AGENT_MAX_TOKENS
from python_backend.core.drafting import DraftingService
from python_backend.infrastructure.llm import LlmEmptyContent, LlmFailure
from tests.conftest import FakeLlm


class _NoMentions:
    """商品指代查证替身:空命中(本模块只看补全预算与重试面,命中面见 test_drafting 的 PG 用例)。"""

    async def find_mentions(self, message: str, *, limit: int) -> tuple[list[dict], bool]:
        return [], False


def _service(llm: FakeLlm) -> DraftingService:
    return DraftingService(llm=llm, vector=None, product_mentions=_NoMentions())


async def test_blank_draft_retries_once_with_shared_budget() -> None:
    """空正文(思考吃穿预算)→ 同预算原样再问一次:第二次拿到正文即正常返回。

    依据(#65):``cs-workbench-stock-zh`` 起草失败 500——``max_tokens=2000`` 与思考共享,思考较长时
    正文一个字都挤不出来(同批重跑即过,属偶发)。预算升到与 agent 线同源(``AGENT_MAX_TOKENS``,
    #64 已把这条口径立在作答轮),再补一次同预算重试兜采样抖动。
    """
    llm = FakeLlm(responses=[LlmEmptyContent("LLM 返回空内容(finish_reason=length)"), "亲,包裹已发出。"])
    result = await _service(llm).draft(message="包裹到哪了?", locale="zh")

    assert result["draft"] == "亲,包裹已发出。"
    assert len(llm.calls) == 2
    assert [call["max_tokens"] for call in llm.calls] == [AGENT_MAX_TOKENS, AGENT_MAX_TOKENS]
    # 原样重问:两次请求的消息一致(空正文不是历史里的错误轮次,没有可修的轮)
    assert llm.calls[0]["messages"] == llm.calls[1]["messages"]


async def test_blank_draft_twice_propagates_after_one_retry() -> None:
    """两次都空:如实上抛 → 端点 500——「宁可不给也不给空草稿」的姿态不变,重试只一次。"""
    llm = FakeLlm(responses=[LlmEmptyContent("空"), LlmEmptyContent("空")])

    with pytest.raises(LlmEmptyContent):
        await _service(llm).draft(message="包裹到哪了?", locale="zh")

    assert len(llm.calls) == 2  # 只重试一次,不循环


async def test_other_llm_failure_is_not_retried() -> None:
    """别的 LlmFailure(429/5xx/网络)不在此重试——传输层已按 1s/2s 退避重试过,再重试是重复。"""
    llm = FakeLlm(responses=[LlmFailure("LLM 调用失败:HTTP 503")])

    with pytest.raises(LlmFailure):
        await _service(llm).draft(message="包裹到哪了?", locale="zh")

    assert len(llm.calls) == 1
