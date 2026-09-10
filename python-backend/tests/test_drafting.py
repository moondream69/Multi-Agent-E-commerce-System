"""起草工作台测试(spec #8 B11/B19):查证先行硬约束 + 多语草稿 + 端点形状。

- 单测:FAQ 必查(假向量计数)、LLM 提示词携带证据与目标语言、证据如实呈现
- 集成(真 PG):提供订单号则订单证据入块;订单不存在 → order null 不编造
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from python_backend.api.app import create_app
from python_backend.core.drafting import DraftingService
from python_backend.db.models import Order, Product
from python_backend.db.session import SessionFactory
from tests.conftest import FakeLlm


class FakeEmbedding:
    """假向量化:返回固定向量,计数调用。"""

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += len(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeVector:
    """假向量仓库:脚本化命中,计数 search 调用。"""

    def __init__(self, hits: list[dict] | None = None) -> None:
        self.hits = hits or []
        self.searches: list[str] = []

    async def search(self, collection: str, vector: list[float], *, top_k: int, filter: str | None = None) -> list:
        self.searches.append(collection)
        return [type("Hit", (), {"id": h["id"], "score": h["score"], "payload": h["payload"]})() for h in self.hits]


def _drafting(llm: FakeLlm, vector: FakeVector | None = None) -> DraftingService:
    return DraftingService(llm=llm, vector=vector, embedding=FakeEmbedding())


async def test_faq_search_always_runs_and_evidence_reaches_prompt() -> None:
    """查证先行:FAQ 必查(B12 端点级),命中证据进提示词,草稿语言=目标语言。"""
    vector = FakeVector(hits=[{"id": "f1", "score": 0.9, "payload": {"question": "物流时效?", "answer": "7-15 天"}}])
    llm = FakeLlm(responses=["ご注文ありがとうございます。発送まで7〜15日です。"])
    service = _drafting(llm, vector)
    result = await service.draft(message="How long is shipping?", locale="ja")
    assert vector.searches == ["faq"]  # 查证先行且必跑
    assert result["draft"].startswith("ご注文")
    prompt = llm.calls[0]["messages"][1]["content"]
    assert "物流时效" in prompt  # 证据进提示词(先引用证据再作答)
    assert "日语" in llm.calls[0]["messages"][0]["content"]  # B19:目标语言


async def test_evidence_empty_when_no_hits_no_fabrication() -> None:
    """查证无命中:证据块如实为空,草稿仍生成(提示词要求如实说明)。"""
    llm = FakeLlm(responses=["查证未命中,请您提供更多信息。"])
    service = _drafting(llm, FakeVector(hits=[]))
    result = await service.draft(message="Where is my parcel?", locale="zh")
    assert result["evidence"]["faq_hits"] == []
    assert result["evidence"]["order"] is None


async def test_infrastructure_failure_propagates() -> None:
    """向量基础设施运行时异常如实上抛(永不静默吞错):查证失败即起草失败。"""

    class ExplodingVector(FakeVector):
        async def search(self, collection: str, vector: list[float], *, top_k: int, filter: str | None = None) -> list:
            raise ConnectionError("Milvus 不可达")

    service = _drafting(FakeLlm(responses=["x"]), ExplodingVector())
    with pytest.raises(ConnectionError):
        await service.draft(message="物流时效?", locale="zh")


async def test_drafting_rejects_bad_locale_and_empty_message() -> None:
    """参数校验:空消息/不支持语言 → DraftingError。"""
    service = _drafting(FakeLlm(responses=["x"]))
    with pytest.raises(Exception, match="不支持的语言"):
        await service.draft(message="hi", locale="xx")
    with pytest.raises(Exception, match="买家消息为空"):
        await service.draft(message="   ", locale="zh")


async def test_drafting_endpoint_shape_and_errors() -> None:
    """端点:200 返回 {draft, evidence};非法语言 422。"""
    client = AsyncClient(
        transport=ASGITransport(app=create_app(auth_required=False, drafting=_drafting(FakeLlm(responses=["草稿"])))),
        base_url="http://test",
    )
    async with client:
        ok = await client.post("/api/drafting", json={"message": "你好", "locale": "zh"})
        assert ok.status_code == 200
        assert set(ok.json()) == {"draft", "evidence"}

        bad = await client.post("/api/drafting", json={"message": "你好", "locale": "xx"})
        assert bad.status_code == 422


# —— 集成(真 PG,离线秒 skip) ——


pytestmark = pytest.mark.usefixtures("requires_postgres")


async def test_order_evidence_included_and_missing_order_not_fabricated() -> None:
    """提供订单号:订单证据入块;订单不存在 → order null(如实呈现「未查到」)。"""
    async with SessionFactory() as session, session.begin():
        product = Product(sku=f"SKU-{uuid.uuid4().hex[:8]}", title="物流查询品", price=Decimal("5.00"), category="测试")
        session.add(product)
        await session.flush()
        order_row = Order(product_id=product.id, total_amount=Decimal("5.00"), currency="USD")
        session.add(order_row)
        await session.flush()
        order_id = order_row.id

    llm = FakeLlm(responses=["查到订单了。", "没查到订单。"])
    service = _drafting(llm)
    found = await service.draft(message="我的订单到哪了?", locale="zh", order_id=order_id)
    assert found["evidence"]["order"] is not None
    assert found["evidence"]["order"]["id"] == order_id
    assert "订单" in json.dumps(llm.calls[0]["messages"][1]["content"], ensure_ascii=False)

    missing = await service.draft(message="我的订单到哪了?", locale="zh", order_id=99999999)
    assert missing["evidence"]["order"] is None  # 不编造
