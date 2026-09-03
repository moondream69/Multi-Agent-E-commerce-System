"""数据播种(镜像 src/seed/*.ts,增强:幂等——按自然键跳过已存在记录,重复执行不重复插入)。

用法: uv run python -m python_backend.seed
注意:LLM 生成本身每次都会执行(数据是生成式的),幂等只保证数据库不产生重复。
"""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from python_backend.db.models import (
    Customer,
    FaqEmbedding,
    MarketEmbedding,
    Product,
    ProductEmbedding,
    ReplyTemplate,
)
from python_backend.db.session import SessionLocal
from python_backend.infrastructure.embedding import EmbeddingService
from python_backend.infrastructure.llm import LlmService

logger = logging.getLogger(__name__)

CATEGORIES: list[dict[str, Any]] = [
    {"name": "消费电子", "prefix": "ELEC", "count": 25},
    {"name": "服装配饰", "prefix": "CLTH", "count": 25},
    {"name": "家居厨房", "prefix": "HOME", "count": 20},
    {"name": "运动户外", "prefix": "SPRT", "count": 20},
    {"name": "美妆个护", "prefix": "BEAU", "count": 15},
    {"name": "图书媒体", "prefix": "BOOK", "count": 15},
    {"name": "玩具游戏", "prefix": "TOYS", "count": 15},
    {"name": "汽摩配件", "prefix": "AUTO", "count": 15},
]

FAQ_TOPICS = [
    {"topic": "物流与配送", "count": 20, "tags": ["shipping", "delivery", "logistics"]},
    {"topic": "退货与退款", "count": 15, "tags": ["returns", "refunds", "after-sales"]},
    {"topic": "支付方式", "count": 15, "tags": ["payment", "currency", "billing"]},
    {"topic": "关税与清关", "count": 15, "tags": ["customs", "duties", "tax"]},
    {"topic": "产品质量与真伪", "count": 15, "tags": ["quality", "authenticity", "warranty"]},
    {"topic": "尺码与适配", "count": 10, "tags": ["sizing", "fit", "measurements"]},
    {"topic": "售后保修", "count": 10, "tags": ["warranty", "repairs", "support"]},
]

MARKET_TOPICS: list[dict[str, Any]] = [
    {"type": "趋势分析", "categories": ["消费电子", "服装", "家居", "运动", "美妆"], "count": 25},
    {"type": "竞品分析", "categories": ["消费电子", "服装", "家居", "运动", "美妆"], "count": 25},
    {"type": "季节性规律", "categories": ["服装", "运动", "家居", "玩具", "美妆"], "count": 25},
    {
        "type": "行业洞察",
        "categories": ["跨境电商", "拉美市场", "东南亚", "欧洲", "北美"],
        "count": 25,
    },
]

REPLY_TEMPLATES = [
    {
        "id": "greeting",
        "scenario": "问候",
        "template": "您好!感谢您联系客服团队,我是您的专属客服助手。请问有什么可以帮助您的?",
        "locale": "zh-CN",
        "variables": [],
    },
    {
        "id": "order_status",
        "scenario": "订单查询",
        "template": "您的订单 #{order_id} 当前状态为: {order_status}。预计{delivery_date}送达。",
        "locale": "zh-CN",
        "variables": ["order_id", "order_status", "delivery_date"],
    },
    {
        "id": "return_policy",
        "scenario": "退换货",
        "template": "我们支持30天无理由退换货。请确保商品完好,申请后3个工作日内处理。",
        "locale": "zh-CN",
        "variables": [],
    },
    {
        "id": "escalation",
        "scenario": "升级工单",
        "template": "您的问题已转接至高级客服专员,将在24小时内通过邮件与您联系。",
        "locale": "zh-CN",
        "variables": [],
    },
]

CUSTOMERS = [
    {
        "name": "张伟",
        "email": "zhangwei@example.com",
        "locale": "zh-CN",
        "preferences": {"preferredCurrency": "CNY", "notificationChannel": "email"},
    },
    {
        "name": "Li Na",
        "email": "lina@example.com",
        "locale": "zh-CN",
        "preferences": {"preferredCurrency": "USD", "notificationChannel": "sms"},
    },
    {
        "name": "James Chen",
        "email": "james.chen@example.com",
        "locale": "en-US",
        "preferences": {"preferredCurrency": "USD", "notificationChannel": "email"},
    },
    {
        "name": "Emily Wang",
        "email": "emily.w@example.com",
        "locale": "en-US",
        "preferences": {"preferredCurrency": "USD", "notificationChannel": "email"},
    },
    {
        "name": "王芳",
        "email": "wangfang@example.com",
        "locale": "zh-CN",
        "preferences": {"preferredCurrency": "CNY", "notificationChannel": "wechat"},
    },
    {
        "name": "刘洋",
        "email": "liuyang@example.com",
        "locale": "zh-CN",
        "preferences": {"preferredCurrency": "CNY", "notificationChannel": "email"},
    },
    {
        "name": "田中太郎",
        "email": "tanaka@example.jp",
        "locale": "ja-JP",
        "preferences": {"preferredCurrency": "JPY", "notificationChannel": "email"},
    },
    {
        "name": "Sakura Yamamoto",
        "email": "sakura@example.jp",
        "locale": "ja-JP",
        "preferences": {"preferredCurrency": "JPY", "notificationChannel": "sms"},
    },
    {
        "name": "Michael Smith",
        "email": "michael.s@example.com",
        "locale": "en-US",
        "preferences": {"preferredCurrency": "USD", "notificationChannel": "email"},
    },
    {
        "name": "Sarah Johnson",
        "email": "sarah.j@example.com",
        "locale": "en-US",
        "preferences": {"preferredCurrency": "USD", "notificationChannel": "email"},
    },
    {
        "name": "Hans Mueller",
        "email": "hans.m@example.de",
        "locale": "de-DE",
        "preferences": {"preferredCurrency": "EUR", "notificationChannel": "email"},
    },
    {
        "name": "Anna Schmidt",
        "email": "anna.s@example.de",
        "locale": "de-DE",
        "preferences": {"preferredCurrency": "EUR", "notificationChannel": "sms"},
    },
    {
        "name": "陈明",
        "email": "chenming@example.com",
        "locale": "zh-CN",
        "preferences": {"preferredCurrency": "CNY", "notificationChannel": "wechat"},
    },
    {
        "name": "赵丽",
        "email": "zhaoli@example.com",
        "locale": "zh-CN",
        "preferences": {"preferredCurrency": "CNY", "notificationChannel": "email"},
    },
    {
        "name": "Pierre Dupont",
        "email": "pierre.d@example.fr",
        "locale": "fr-FR",
        "preferences": {"preferredCurrency": "EUR", "notificationChannel": "email"},
    },
    {
        "name": "Marie Laurent",
        "email": "marie.l@example.fr",
        "locale": "fr-FR",
        "preferences": {"preferredCurrency": "EUR", "notificationChannel": "sms"},
    },
    {
        "name": "David Lee",
        "email": "david.lee@example.com",
        "locale": "en-US",
        "preferences": {"preferredCurrency": "USD", "notificationChannel": "email"},
    },
    {
        "name": "佐藤健",
        "email": "sato@example.jp",
        "locale": "ja-JP",
        "preferences": {"preferredCurrency": "JPY", "notificationChannel": "email"},
    },
    {
        "name": "黄晓明",
        "email": "huangxm@example.com",
        "locale": "zh-CN",
        "preferences": {"preferredCurrency": "CNY", "notificationChannel": "sms"},
    },
    {
        "name": "周杰",
        "email": "zhoujie@example.com",
        "locale": "zh-CN",
        "preferences": {"preferredCurrency": "CNY", "notificationChannel": "email"},
    },
]

LLM = LlmService()
EMBEDDING = EmbeddingService()


def _parse_array(raw: str) -> list[dict]:
    try:
        items = json.loads(raw)
        return items if isinstance(items, list) else []
    except Exception:
        return []


def seed_products() -> int:
    added = 0
    for cat in CATEGORIES:
        print(f"生成 {cat['name']} 商品 ({cat['count']}个)...", flush=True)
        raw = LLM.complete(
            [
                {
                    "role": "system",
                    "content": "你是一个跨境电商商品数据生成器。生成逼真的商品数据,所有价格使用美元(USD)。",
                },
                {
                    "role": "user",
                    "content": (
                        f'生成 {cat["count"]} 个"{cat["name"]}"类目的商品JSON数组。每个商品严格包含以下字段:\n'
                        f'- sku: "{cat["prefix"]}-" 前缀加3位数字 (如 "{cat["prefix"]}-001")\n'
                        "- title: 简短商品名 (英文, 最多80字符)\n"
                        "- description: 1-2句商品描述 (中文, 强调卖点和规格)\n"
                        "- price: 美元价格 (数字, 合理范围)\n"
                        f'- category: "{cat["name"]}"\n'
                        '- currency: "USD"\n'
                        '- platform: 随机选择 "Amazon" / "eBay" / "Shopify"\n'
                        '- status: "active"\n\n只返回有效的JSON数组,不要其他文字。'
                    ),
                },
            ],
            temperature=0.7,
            max_tokens=8000,
            json_mode=True,
        )
        for item in _parse_array(raw):
            try:
                with SessionLocal() as session:
                    if session.scalar(select(Product.id).where(Product.sku == item["sku"])):
                        continue
                    product = Product(
                        sku=item["sku"],
                        title=item["title"],
                        description=item.get("description") or "",
                        price=Decimal(str(item["price"])),
                        category=item["category"],
                        currency=item.get("currency") or "USD",
                        platform=item.get("platform") or "Amazon",
                        status=item.get("status") or "active",
                    )
                    session.add(product)
                    session.flush()
                    embed_text = f"{item['title']} {item.get('description') or ''}"
                    vector = EMBEDDING.embed(embed_text)
                    session.add(
                        ProductEmbedding(
                            productId=str(product.id),
                            embedding=vector,
                            content=embed_text,
                            metadata_={
                                "price": item["price"],
                                "category": item["category"],
                                "platform": item.get("platform"),
                            },
                        )
                    )
                    session.commit()
                    added += 1
            except Exception as error:
                print(f"跳过商品 {item.get('sku')}: {error}", flush=True)
    return added


def seed_market() -> int:
    added = 0
    for topic in MARKET_TOPICS:
        print(f"生成市场情报: {topic['type']}...", flush=True)
        raw = LLM.complete(
            [
                {
                    "role": "system",
                    "content": "你是跨境电商市场分析专家。生成简洁、数据驱动的市场情报条目。",
                },
                {
                    "role": "user",
                    "content": (
                        f'生成 {topic["count"]} 条"{topic["type"]}"类市场情报JSON数组,覆盖类目: {", ".join(map(str, topic["categories"]))}。\n\n'  # noqa: E501
                        "每条包含:\n"
                        "- content: 情报内容 (中文, 100-200字, 含具体数字和数据)\n"
                        "- category: 所属类目\n"
                        '- source: 数据来源 (如 "Google Trends" / "Jungle Scout" / "海关数据" / "行业报告")\n\n只返回有效JSON数组。'  # noqa: E501
                    ),
                },
            ],
            temperature=0.7,
            max_tokens=8000,
            json_mode=True,
        )
        for item in _parse_array(raw):
            try:
                with SessionLocal() as session:
                    if session.scalar(select(MarketEmbedding.id).where(MarketEmbedding.content == item["content"])):
                        continue
                    vector = EMBEDDING.embed(item["content"])
                    session.add(
                        MarketEmbedding(
                            source=item.get("source") or "行业报告",
                            content=item["content"],
                            embedding=vector,
                            category=item.get("category") or "综合",
                            collectedAt=datetime.now(UTC).date() - timedelta(days=random.randint(0, 365)),
                        )
                    )
                    session.commit()
                    added += 1
            except Exception as error:
                print(f"跳过市场条目: {error}", flush=True)
    return added


def seed_faq() -> int:
    added = 0
    for topic in FAQ_TOPICS:
        print(f"生成FAQ: {topic['topic']}...", flush=True)
        raw = LLM.complete(
            [
                {"role": "system", "content": "你是跨境电商客服专家。生成高质量的FAQ问答对。"},
                {
                    "role": "user",
                    "content": (
                        f'生成 {topic["count"]} 对关于"{topic["topic"]}"的FAQ JSON数组。\n\n'
                        "每条严格包含:\n"
                        "- question: 客户常问问题 (中文或英文, 自然语言, 10-40字)\n"
                        "- answer: 客服标准回答 (中文, 50-150字, 专业友好)\n"
                        f'- locale: "en" (英文问题) 或 "zh-CN" (中文问题)\n'
                        f"- tags: {json.dumps(topic['tags'])}\n\n混合中英文问题。只返回有效JSON数组。"
                    ),
                },
            ],
            temperature=0.7,
            max_tokens=8000,
            json_mode=True,
        )
        for item in _parse_array(raw):
            try:
                with SessionLocal() as session:
                    if session.scalar(
                        select(FaqEmbedding.id).where(
                            FaqEmbedding.question == item["question"],
                            FaqEmbedding.locale == (item.get("locale") or "zh-CN"),
                        )
                    ):
                        continue
                    embed_text = f"Q: {item['question']}\nA: {item['answer']}"
                    vector = EMBEDDING.embed(embed_text)
                    session.add(
                        FaqEmbedding(
                            question=item["question"],
                            answer=item["answer"],
                            embedding=vector,
                            locale=item.get("locale") or "zh-CN",
                            tags=item.get("tags") or topic["tags"],
                        )
                    )
                    session.commit()
                    added += 1
            except Exception as error:
                print(f"跳过FAQ: {error}", flush=True)
    return added


def seed_customers() -> int:
    print(f"生成 {len(CUSTOMERS)} 个客户...", flush=True)
    added = 0
    for item in CUSTOMERS:
        with SessionLocal() as session:
            if session.scalar(select(Customer.id).where(Customer.email == item["email"])):
                continue
            session.add(Customer(**item))
            session.commit()
            added += 1
    return added


def seed_templates() -> int:
    """回复模板播种(离线,无 LLM):按自然键 (scenario, locale) 幂等。"""
    print(f"生成 {len(REPLY_TEMPLATES)} 条回复模板...", flush=True)
    added = 0
    for item in REPLY_TEMPLATES:
        with SessionLocal() as session:
            if session.scalar(
                select(ReplyTemplate.id).where(
                    ReplyTemplate.scenario == item["scenario"],
                    ReplyTemplate.locale == item["locale"],
                )
            ):
                continue
            session.add(ReplyTemplate(**item))
            session.commit()
            added += 1
    return added


def main() -> None:
    start = time.time()
    product_count = seed_products()
    market_count = seed_market()
    faq_count = seed_faq()
    customer_count = seed_customers()
    template_count = seed_templates()

    elapsed = f"{time.time() - start:.1f}"
    total = product_count + market_count + faq_count + customer_count + template_count
    print("\n========================================")
    print(f"  播种完成 ({elapsed}s)")
    print("========================================")
    print(f"  商品 + 向量    : {product_count}")
    print(f"  市场情报 + 向量: {market_count}")
    print(f"  FAQ + 向量     : {faq_count}")
    print(f"  客户           : {customer_count}")
    print(f"  回复模板       : {template_count}")
    print("  -------------------------------------")
    print(f"  总计           : {total}")
    print("========================================")


if __name__ == "__main__":
    main()
