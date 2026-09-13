"""起草五语离线证据(issue #43,验收 B19):逐语 locale 名进提示词 + 证据块注入 + 非法语言拒绝。

test_drafting.py 全模块经 requires_postgres 门控(#39 商品指代查证走真库),离线全套跳过;
本模块把四件依赖全替身化(llm/vector/embedding/商品指代),让五语逐语证据进 CI 快速套件(不耗预算)。
"""

from __future__ import annotations

import pytest

from python_backend.core.drafting import SUPPORTED_LOCALES, DraftingError, DraftingService
from tests.conftest import FakeLlm

# 逐语矩阵:locale 代码 / 提示词里的语言名 / 对应语种的买家消息
_LOCALES = [
    ("zh", "中文", "包裹到哪了?"),
    ("en", "英语", "Where is my parcel?"),
    ("ja", "日语", "荷物はどこですか?"),
    ("de", "德语", "Wo ist mein Paket?"),
    ("fr", "法语", "Où est mon colis?"),
]


class _FakeEmbedding:
    """假向量化:固定向量(与 test_drafting.FakeEmbedding 同形)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeVector:
    """假向量仓库:脚本化 FAQ 命中(与 test_drafting.FakeVector 同形)。"""

    def __init__(self, hits: list[dict] | None = None) -> None:
        self.hits = hits or []

    async def search(self, collection: str, vector: list[float], *, top_k: int, filter: str | None = None) -> list:
        return [type("Hit", (), {"id": h["id"], "score": h["score"], "payload": h["payload"]})() for h in self.hits]


class _NoMentions:
    """商品指代查证替身:空命中——五语证据只关心语言面,商品命中面见 test_drafting 的 PG 用例。"""

    async def find_mentions(self, message: str, *, limit: int) -> tuple[list[dict], bool]:
        return [], False


def _service(llm: FakeLlm, hits: list[dict] | None = None) -> DraftingService:
    return DraftingService(
        llm=llm,
        vector=_FakeVector(hits),
        embedding=_FakeEmbedding(),
        product_mentions=_NoMentions(),
    )


def test_locale_matrix_covers_supported_set() -> None:
    """参数化矩阵与 SUPPORTED_LOCALES 同步:新增语种时必须同步本文件(防静默漏证)。"""
    assert {locale for locale, _name, _message in _LOCALES} == set(SUPPORTED_LOCALES)


@pytest.mark.parametrize(("locale", "locale_name", "message"), _LOCALES)
async def test_each_locale_prompts_in_target_language_with_evidence(
    locale: str, locale_name: str, message: str
) -> None:
    """B19 逐语:目标语言名与 locale 代码进系统提示词,买家消息与查证证据进用户提示词,草稿原样返回。"""
    llm = FakeLlm(responses=[f"[{locale}] 草稿正文"])
    hits = [{"id": "f1", "score": 0.9, "payload": {"question": "物流时效?", "answer": "7-15 天"}}]
    service = _service(llm, hits=hits)

    result = await service.draft(message=message, locale=locale)

    system = llm.calls[0]["messages"][0]["content"]
    user = llm.calls[0]["messages"][1]["content"]
    assert locale_name in system, "目标语言名进系统提示词"
    assert f"locale 代码 {locale}" in system, "locale 代码进系统提示词(防同名语种歧义)"
    assert message in user, "买家消息原文进用户提示词"
    assert "物流时效" in user, "查证证据块注入(逐语同口径,先引用证据再作答)"
    assert result["draft"] == f"[{locale}] 草稿正文"
    assert result["evidence"]["faq_hits"][0]["id"] == "f1"


async def test_unsupported_locale_rejected_with_legal_list() -> None:
    """非法语言拒绝:报错列出全部合法语种(B19 五语口径;与端点 422 的 DraftingError 同源)。"""
    service = _service(FakeLlm(responses=["x"]))

    with pytest.raises(DraftingError) as excinfo:
        await service.draft(message="hi", locale="xx")

    for locale in SUPPORTED_LOCALES:
        assert locale in str(excinfo.value), "拒绝信息须列出合法语种,不静默"
