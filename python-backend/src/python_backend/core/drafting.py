"""起草服务(spec #8 B11/B19):查证优先 + 多语草稿,专用端点不沾任务流。

- 查证先行硬约束(B12 端点级延续):faq_search 必跑、提供订单号则 order_lookup 同跑、
  商品指代按消息文本查库(issue #39),代码顺序保证查证先于草稿;证据块随草稿返回,
  查证无命中如实呈现「未查到」
- 多语(B19):zh/en/ja/de/fr;草稿由 LLM 按目标语言生成,提示词携带证据、禁编造
- 不落库(起草助手定位:人工编辑后复制即走,无发件箱语义)
"""

from __future__ import annotations

import json
from typing import Protocol

from python_backend.db.models import Order, Product
from python_backend.db.product_lookup import (
    MENTION_MATCH_LIMIT,
    PostgresProductMentionSearcher,
    ProductMentionSearcher,
)
from python_backend.db.session import SessionFactory
from python_backend.infrastructure.embedding import EmbeddingClient, EmbeddingService
from python_backend.infrastructure.llm import LlmClient, LlmService

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
        """查证 → 草稿:返回 {draft, evidence}。查证先行,草稿提示词携带证据。"""
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

        # 3) LLM 草稿(目标语言;证据不足须如实说明)
        locale_name = _LOCALE_NAMES[locale]
        draft = await self._llm.complete(
            [
                {
                    "role": "system",
                    "content": (
                        f"你是跨境电商客服起草助手。请用{locale_name}(locale 代码 {locale})起草一条回复买家消息的话术:"
                        "先引用查证证据再作答;证据不足或未查到相关信息时,必须如实说明,不得编造事实。"
                        "语气专业友善,只输出回复正文。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"买家消息:{message}\n\n查证证据:{json.dumps(evidence, ensure_ascii=False, default=str)}"
                    ),
                },
            ],
            max_tokens=2000,  # 思考模式推理与正文共享预算:低预算(原 800)有饿空正文风险,对齐默认档
        )
        return {"draft": draft, "evidence": evidence}
