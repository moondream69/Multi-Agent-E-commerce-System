"""起草线三类证据统一编号与系统记录引用(issue #67):ref 贯穿 FAQ/订单/商品,标注可锚到商品。

``test_drafting.py`` 全模块经 requires_postgres 门控(#39 商品指代查证走真库),故按
``test_drafting_blank.py`` / ``test_drafting_locales.py`` 同法另立模块:只补需要的替身,
让「编序 → 标注 → 归一化 → 记录条目」这条链进 CI 快速套件。
"""

from __future__ import annotations

from python_backend.core.drafting import DraftingService, _numbered_sources
from tests.conftest import FakeLlm


class _FakeEmbedding:
    """假向量化:固定向量(与 test_drafting_locales 同形)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeVector:
    """假向量仓库:脚本化 FAQ 命中。"""

    def __init__(self, hits: list[dict]) -> None:
        self.hits = hits

    async def search(self, collection: str, vector: list[float], *, top_k: int, filter: str | None = None) -> list:
        return [type("Hit", (), {"id": h["id"], "score": h["score"], "payload": h["payload"]})() for h in self.hits]


class _Mentions:
    """商品指代查证替身:脚本化命中(形状 = PostgresProductMentionSearcher 的载荷)。"""

    def __init__(self, products: list[dict] | None = None, *, truncated: bool = False) -> None:
        self.products = products or []
        self.truncated = truncated

    async def find_mentions(self, message: str, *, limit: int) -> tuple[list[dict], bool]:
        return self.products, self.truncated


def _product(product_id: int = 82, *, stock: int = 2) -> dict:
    """商品证据(形状 = ``db/product_lookup._product_payload``)。"""
    return {
        "id": product_id,
        "sku": "SYN-HM-081",
        "title": "桌面收纳架 深空黑款",
        "price": "129.00",
        "currency": "CNY",
        "category": "家居",
        "status": "draft",
        "stock": stock,
    }


def _faq_hit(chunk_id: str, *, content: str) -> dict:
    return {
        "id": chunk_id,
        "score": 0.56,
        "payload": {
            "doc_id": chunk_id.split("#", 1)[0],
            "title": "下单后多久发货?",
            "source": "自造 FAQ 语料库",
            "published_at": "2026-09-14",
            "section": "物流配送",
            "chunk_index": 1,
            "content": content,
        },
    }


def _service(llm: FakeLlm, *, hits: list[dict], products: list[dict], truncated: bool = False) -> DraftingService:
    return DraftingService(
        llm=llm,
        vector=_FakeVector(hits),
        embedding=_FakeEmbedding(),
        product_mentions=_Mentions(products, truncated=truncated),
    )


async def test_faq_and_product_evidence_share_one_number_sequence() -> None:
    """#67:FAQ 命中与商品命中**同一序编号**(1..F → 商品),草稿标注按该序解析。

    依据:上轮实评 ``cs-workbench-stock-zh#1#2/#1#3``——商品证据没有编号可标,草稿的库存结论
    既无 `[n]` 可对、又无证据块可核,判分材料里结构性不可核验。
    """
    hits = [_faq_hit("faq-logistics#1", content="Q: 下单后多久发货?\nA: 现货 48 小时内发出。")]
    llm = FakeLlm(responses=["现货商品 48 小时内发出[1];该款当前库存仅剩 2 件[2]。"])
    service = _service(llm, hits=hits, products=[_product()])

    result = await service.draft(message="桌面收纳架 深空黑款还有货吗?什么时候发货?", locale="zh")

    user = llm.calls[0]["messages"][1]["content"]
    assert '"ref": 1' in user and '"ref": 2' in user  # 两类证据都带编号(草稿可标)
    assert '"ref": 3' not in user  # 只有两款命中,不给不存在的号
    assert [entry["number"] for entry in result["citations"]] == [1, 2]
    assert result["citations"][0]["kind"] == "corpus"
    assert result["citations"][1]["kind"] == "product"
    assert result["citations"][1]["record"] == {
        "id": "product:82",
        "content": "SKU SYN-HM-081 · 价格 129.00 CNY · 状态 draft · 库存 2",
    }
    assert result["draft"] == "现货商品 48 小时内发出[1];该款当前库存仅剩 2 件[2]。"  # 归一化后编号不变


async def test_numbering_is_the_single_ordering_point_order_sits_in_the_middle() -> None:
    """**编序点唯一**(#67):FAQ 1..F → 订单 → 商品,序号即引用源下标 + 1。

    订单查证走真库(``_order_evidence`` 经 SessionFactory),离线跑不了 draft 全链,故直接
    对编序函数取证:序号与引用源若两处分开编序,就会出现「标了号却锚到别人身上」的静默错锚。
    """
    prompt, sources = _numbered_sources(
        {
            "faq_hits": [_faq_hit("faq-logistics#1", content="A"), _faq_hit("faq-logistics#2", content="B")],
            "order": {"id": 1042, "status": "shipped", "total_amount": "299.00", "currency": "CNY", "product": None},
            "order_id": 1042,
            "products": [_product()],
            "products_truncated": False,
        }
    )

    assert [hit["ref"] for hit in prompt["faq_hits"]] == [1, 2]
    assert prompt["order"]["ref"] == 3
    assert prompt["products"][0]["ref"] == 4
    assert len(sources) == 4
    assert sources[3]["kind"] == "product"  # 序号 4 → 商品记录(与该下标同源)
    assert sources[2]["record"]["id"] == "order:1042"


async def test_evidence_returned_to_frontend_carries_no_ref() -> None:
    """ref 是**提示词侧的教具**,不是证据的属性:返回前端的证据块不带它(载荷不掺提示词细节)。"""
    llm = FakeLlm(responses=["该款当前库存仅剩 2 件。"])
    service = _service(llm, hits=[], products=[_product()])

    result = await service.draft(message="桌面收纳架 深空黑款还有货吗?", locale="zh")

    assert "ref" not in result["evidence"]["products"][0]
    assert result["citations"] == []  # 草稿没标注:无引用条目(有证据不等于有标注)


async def test_truncated_product_hits_are_flagged_in_evidence() -> None:
    """命中截断随证据块如实上报(前端据此提示「还有更多」;判分材料同源标注,#67)。"""
    llm = FakeLlm(responses=["已按查到的款式答复。"])
    service = _service(llm, hits=[], products=[_product()], truncated=True)

    result = await service.draft(message="收纳架还有货吗?", locale="zh")

    assert result["evidence"]["products_truncated"] is True
