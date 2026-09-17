"""起草服务(spec #8 B11/B19):查证优先 + 多语草稿,专用端点不沾任务流。

- 查证先行硬约束(B12 端点级延续):faq_search 必跑、提供订单号则 order_lookup 同跑、
  商品指代按消息文本查库(issue #39),代码顺序保证查证先于草稿;证据块随草稿返回,
  查证无命中如实呈现「未查到」
- 多语(B19):zh/en/ja/de/fr;草稿由 LLM 按目标语言生成,提示词携带证据、禁编造
- 引用小点(issue #51 / #67):提示词给**三类证据统一编号**(FAQ/订单/商品,ref 贯穿),
  草稿按 ref 标注;返回前归一化为上标编号(同一文档合并,系统记录按记录合并),
  citations 随回答一起下发——商品类结论也能标注,不再只有语料条目可核
- 空正文(issue #65,现由 ``llm.complete_with_blank_retry`` 承担,#68 升为全仓共用):
  思考吃穿预算时**同预算原样再问一次**(与 #64 B1 作答轮护栏同口径),仍空才上抛
  LlmEmptyContent → 端点 500——「宁可不给也不给空草稿」的姿态不变,但可恢复的
  预算问题不再直接变成用户可见的 500
- 不落库(起草助手定位:人工编辑后复制即走,无发件箱语义)
"""

from __future__ import annotations

import json
from typing import Protocol

from python_backend.core.citations import build_citations
from python_backend.db.models import Order, Product
from python_backend.db.product_lookup import (
    MENTION_MATCH_LIMIT,
    PostgresProductMentionSearcher,
    ProductMentionSearcher,
)
from python_backend.db.session import SessionFactory
from python_backend.infrastructure.embedding import EmbeddingClient, EmbeddingService
from python_backend.infrastructure.llm import AGENT_MAX_TOKENS, LlmClient, LlmService, complete_with_blank_retry

SUPPORTED_LOCALES = ("zh", "en", "ja", "de", "fr")

_LOCALE_NAMES = {
    "zh": "中文",
    "en": "英语",
    "ja": "日语",
    "de": "德语",
    "fr": "法语",
}


class DraftingError(Exception):
    """起草参数非法(语言不支持/买家消息为空)。"""


class FaqSearcher(Protocol):
    """FAQ 检索协议(起草服务依赖的向量仓库子集,签名对齐 VectorRepository):测试注入假实现。"""

    async def search(self, collection: str, vector: list[float], *, top_k: int, filter: str | None = None) -> list: ...


async def _faq_evidence(vector: FaqSearcher | None, embedding: EmbeddingClient, message: str) -> list[dict]:
    """FAQ 查证(Milvus faq 集合):无命中 → 空列表(如实呈现,不编造)。

    运行时基础设施异常如实上抛(宪章:永不静默吞错)——查证失败即起草失败,
    不出无证据草稿;仅未装配(vector=None)时证据留空。
    """
    if vector is None:
        return []
    [query_vector] = await embedding.embed([message])
    hits = await vector.search("faq", query_vector, top_k=3)
    return [{"id": hit.id, "score": hit.score, "payload": hit.payload} for hit in hits]


async def _order_evidence(order_id: int) -> dict | None:
    """订单查证:不存在返回 None(证据块如实呈现「未查到」)。"""
    async with SessionFactory() as session:
        order = await session.get(Order, order_id)
        if order is None:
            return None
        product = await session.get(Product, order.product_id)
        return {
            "id": order.id,
            "status": order.status.value,
            "total_amount": str(order.total_amount),
            "currency": order.currency,
            "product": {"id": product.id, "sku": product.sku, "title": product.title} if product else None,
        }


def _order_source(order: dict) -> dict:
    """订单证据 → **系统记录**引用源(#67):记录标识 + 一行可读正文(同 chunk.content 一类,是数据)。

    记录正文在后端拼一次:前端弹层与 judge 材料的**引用条目行**渲染的是同一行,不各写一份;
    judge 的【查证证据】段另有更全的逐字段渲染(未被引的命中也要可核,见 ``evals/judge.py``)。
    """
    product = order.get("product") or {}
    detail = f"订单 #{order['id']} · 状态 {order['status']} · 金额 {order['total_amount']} {order['currency']}"
    if product:
        detail += f" · 商品 {product.get('sku')} {product.get('title')}"
    return {
        "kind": "order",
        "title": f"订单 #{order['id']}",
        "source": "订单库(系统查询结果)",
        "record": {"id": f"order:{order['id']}", "content": detail},
    }


def _product_source(product: dict) -> dict:
    """商品证据 → 系统记录引用源(#67):正文 = 库存/价格/状态等在售事实(判据②③核的就是这些)。"""
    return {
        "kind": "product",
        "title": str(product.get("title") or ""),
        "source": "商品库(系统查询结果)",
        "record": {
            "id": f"product:{product['id']}",
            "content": (
                f"SKU {product.get('sku')} · 价格 {product.get('price')} {product.get('currency')}"
                f" · 状态 {product.get('status')} · 库存 {product.get('stock')}"
            ),
        },
    }


def _numbered_sources(evidence: dict) -> tuple[dict, list[dict]]:
    """证据块 → (提示词视图, 引用源清单):**唯一编序点**(#67)。

    ref 贯穿三类证据:FAQ 命中 1..F、订单(查到时)次之、商品依次其后——**序号即引用源下标 + 1**;
    草稿照它标注、``build_citations`` 照同一份清单解析序号。两处分开编序就会「标了号却锚到别人身上」
    (序号式归一化对越界号是静默失败,错锚比不锚更坏)。返回给前端的 evidence 不带 ref:
    编号是提示词侧的教具,不是证据自身的属性。
    """
    sources: list[dict] = list(evidence["faq_hits"])
    faq_view = [{**hit, "ref": index} for index, hit in enumerate(evidence["faq_hits"], start=1)]
    order_view = None
    if evidence["order"] is not None:
        sources.append(_order_source(evidence["order"]))
        order_view = {**evidence["order"], "ref": len(sources)}
    products_view = []
    for product in evidence["products"]:
        sources.append(_product_source(product))
        products_view.append({**product, "ref": len(sources)})
    prompt_evidence = {**evidence, "faq_hits": faq_view, "order": order_view, "products": products_view}
    return prompt_evidence, sources


async def _draft_completion(llm: LlmClient, messages: list[dict]) -> str:
    """草稿补全:预算与 agent 线同源,空正文走共用的同预算重试(#65 的护栏,#68 提炼到 llm 模块)。

    端点侧仍上抛 ``LlmEmptyContent`` → 500:「宁可不给也不给空草稿」的姿态不变,重试只兜采样抖动。
    """
    return await complete_with_blank_retry(llm, messages, max_tokens=AGENT_MAX_TOKENS)


class DraftingService:
    """起草服务:依赖注入与 ToolExecutor 同风格(llm/vector/embedding/product_mentions,测试假实现)。"""

    def __init__(
        self,
        *,
        llm: LlmClient | None = None,
        vector: FaqSearcher | None = None,
        embedding: EmbeddingClient | None = None,
        product_mentions: ProductMentionSearcher | None = None,
    ) -> None:
        self._llm = llm or LlmService()
        self._vector = vector
        self._embedding = embedding or EmbeddingService()
        self._product_mentions = product_mentions or PostgresProductMentionSearcher()

    async def draft(self, *, message: str, locale: str, order_id: int | None = None) -> dict:
        """查证 → 草稿:返回 {draft, evidence, citations}。查证先行,草稿提示词携带证据。

        issue #51/#67:证据条目在提示词里带 ref 编号(**三类统一编号**,见 ``_numbered_sources``),
        草稿据其标注——商品/订单类结论也能标到记录条目;返回前归一化为上标编号(同文档/同记录合并),
        citations 随回答一起下发(前端自足,无二次请求)。
        """
        if not message.strip():
            raise DraftingError("买家消息为空")
        if locale not in SUPPORTED_LOCALES:
            raise DraftingError(f"不支持的语言:{locale!r}(合法:{'/'.join(SUPPORTED_LOCALES)})")

        # 1) 查证先行(硬约束):FAQ 必查,提供订单号则订单同查,商品指代按消息文本查库
        faq_hits = await _faq_evidence(self._vector, self._embedding, message)
        order = await _order_evidence(order_id) if order_id is not None else None
        products, products_truncated = await self._product_mentions.find_mentions(message, limit=MENTION_MATCH_LIMIT)

        # 2) 证据块(如实呈现:无命中即空,不编造)
        evidence = {
            "faq_hits": faq_hits,
            "order": order,
            "order_id": order_id,
            "products": products,
            "products_truncated": products_truncated,
        }
        # 提示词视图 + 引用源:三类证据统一编号(#67;编序点唯一,见 _numbered_sources)
        prompt_evidence, sources = _numbered_sources(evidence)

        # 3) LLM 草稿(目标语言;证据不足须如实说明)
        locale_name = _LOCALE_NAMES[locale]
        draft = await _draft_completion(
            self._llm,
            [
                {
                    "role": "system",
                    "content": (
                        f"你是跨境电商客服起草助手。请用{locale_name}(locale 代码 {locale})起草一条回复买家消息的话术:"
                        "先引用查证证据再作答;证据不足或未查到相关信息时,必须如实说明,不得编造事实。"
                        "依据查证证据作答时,在该句末尾用方括号标出所依据的证据编号(如 [1]),"
                        "编号取自查证证据的 ref(FAQ 条目、订单、商品均带 ref,皆可标);"
                        "未依据证据的句子不标。语气专业友善,只输出回复正文。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"买家消息:{message}\n\n查证证据:{json.dumps(prompt_evidence, ensure_ascii=False, default=str)}"
                    ),
                },
            ],
        )
        # 4) 引用小点(#51/#67):标记归一化为上标编号(同文档/同记录合并);无引用源即无引用。
        # 起草线在提示词里给证据编了 ref,故放开序号式(客服线只认切块标识,见 citations 模块说明)
        draft, citations = build_citations(draft, sources, allow_ordinals=True)
        return {"draft": draft, "evidence": evidence, "citations": citations}
