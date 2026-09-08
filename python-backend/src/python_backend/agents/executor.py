"""工具执行器与效果后置执行(apply)(spec #7:业务子图 + 真实工具挂接)。

三层分类接线(宪章 B7):
- execute(action, params):auto 动作直行(子图 tool 节点调用)
- capture(action, params):approval 动作读取现状快照(B18 apply 漂移比对基准)
- apply_approved_actions(actions):事务内执行一批已批动作(行锁 + 快照比对,批内同进同退)
- forbidden 动作无处理函数,且不出现在任何节点授权集(LLM 不可见)

工具名(LLM function 名,snake_case)与动作标识(分类表)经 `action_of` 显式映射:
仅草稿与对外状态变更动作使用 dotted 标识(增量 3 钉死),其余同名。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from sqlalchemy import func, select

from python_backend.db.models import (
    ApprovalBatch,
    ApprovalStatus,
    Order,
    OrderStatus,
    Product,
    ProductStatus,
    ReplyTemplate,
    Ticket,
)
from python_backend.db.session import SessionFactory
from python_backend.infrastructure.embedding import EmbeddingService
from python_backend.infrastructure.llm import LlmClient, LlmService
from python_backend.vector_repo.base import VectorRepository

# 工具名 → 动作标识:仅草稿与对外状态变更动作用 dotted 标识(增量 3 钉死),
# 其余工具动作标识与工具名相同(如 list_orders / faq_search)。
_ACTION_IDS = {
    "draft_create": "draft.create",
    "draft_edit": "draft.edit",
    "product_publish": "product.publish",
    "product_unpublish": "product.unpublish",
    "product_update_price": "product.update_price",
    "product_delete": "product.delete",
    "order_transition": "order.transition",
    "order_cancel": "order.cancel",
}


def action_of(tool_name: str) -> str:
    """工具名 → 动作标识(显式映射;未知工具名原样返回,由分类表判 forbidden)。"""
    return _ACTION_IDS.get(tool_name, tool_name)


# 订单七态状态机(宪章 A6):pending/confirmed 均可 → cancelled;cancelled/returned 为终态
VALID_TRANSITIONS: dict[OrderStatus, list[OrderStatus]] = {
    OrderStatus.PENDING: [OrderStatus.CONFIRMED, OrderStatus.CANCELLED],
    OrderStatus.CONFIRMED: [OrderStatus.PROCESSING, OrderStatus.CANCELLED],
    OrderStatus.PROCESSING: [OrderStatus.SHIPPED],
    OrderStatus.SHIPPED: [OrderStatus.DELIVERED],
    OrderStatus.DELIVERED: [OrderStatus.RETURNED],
    OrderStatus.CANCELLED: [],
    OrderStatus.RETURNED: [],
}


class ApplyConflict(Exception):
    """apply 冲突(快照漂移/非法流转/引用约束):批内同进同退,整批不执行。"""


@dataclass
class ApplyResult:
    applied: bool
    reason: str | None = None


# apply 函数注入接缝(spec #7):生产 apply_batch_actions(事务+快照比对),测试注入假实现
ApplyFunction = Callable[[str, list[dict]], Awaitable[ApplyResult]]


class Executor(Protocol):
    """工具执行器协议:子图依赖此协议而非具体实现(测试注入假执行器)。"""

    async def execute(self, action: str, params: dict) -> dict: ...

    async def capture(self, action: str, params: dict) -> dict: ...


class EmbeddingClient(Protocol):
    """向量化协议(与 infrastructure.embedding.EmbeddingService 结构一致,测试注入假实现)。"""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class ToolExecutor:
    """真实执行器:DB 工具直连 SessionFactory,LLM/向量工具依赖注入的服务。"""

    def __init__(
        self,
        *,
        llm: LlmClient | None = None,
        vector: VectorRepository | None = None,
        embedding: EmbeddingClient | None = None,
    ) -> None:
        self._llm = llm or LlmService()
        self._vector = vector
        self._embedding = embedding or EmbeddingService()

    # —— execute:auto 动作直行 ——

    async def execute(self, action: str, params: dict) -> dict:
        handler = _EXECUTE_HANDLERS.get(action)
        if handler is None:
            raise ValueError(f"未知动作:{action}")
        return await handler(self, params)

    async def _execute_translate(self, params: dict) -> dict:
        text, locale = params["text"], params["target_locale"]
        translated = await self._llm.complete(
            [
                {"role": "system", "content": f"你是专业翻译。请把用户文本翻译为 {locale},保持语气自然。"},
                {"role": "user", "content": text},
            ],
            temperature=0.3,
            max_tokens=500,
        )
        return {"translated": translated}

    async def _execute_scoring(self, params: dict) -> dict:
        raw = await self._llm.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "你是选品评分专家。根据商品信息从市场潜力、竞争程度、利润率、运营难度四维评分,"
                        '仅输出 JSON:{"score": 0-100 整数, "grade": "A/B/C/D", "rationale": "一句话理由"}'
                    ),
                },
                {"role": "user", "content": json.dumps(params, ensure_ascii=False)},
            ],
            json_mode=True,
            max_tokens=300,
        )
        return json.loads(raw)

    async def _execute_generate_report(self, params: dict) -> dict:
        report = await self._llm.complete(
            [
                {"role": "system", "content": "你是选品分析师,请基于给定情报生成结构化选品分析报告(含结论与风险)。"},
                {"role": "user", "content": params.get("context", "")},
            ],
            max_tokens=2000,
        )
        return {"report": report}

    async def _execute_sentiment_analysis(self, params: dict) -> dict:
        sentiment = await self._llm.complete(
            [
                {
                    "role": "system",
                    "content": "判断买家消息情绪,仅输出一个词:positive / negative / neutral",
                },
                {"role": "user", "content": params["text"]},
            ],
            temperature=0.1,
            max_tokens=10,
        )
        return {"sentiment": sentiment.strip()}

    async def _execute_generate_draft(self, params: dict) -> dict:
        draft = await self._llm.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "你是跨境电商客服起草助手。基于买家消息与查证证据起草一条回复"
                        "(语言与买家消息一致,语气专业友善);先引用证据再作答,证据不足时如实说明。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"买家消息:{params['buyer_message']}\n\n查证证据:{params.get('evidence', '')}",
                },
            ],
            max_tokens=800,
        )
        return {"draft": draft}

    async def _execute_search(self, collection: str, query: str, top_k: int = 5) -> dict:
        if self._vector is None:
            raise ValueError(f"向量仓库未注入({collection} 检索不可用)")
        [vector] = await self._embedding.embed([query])
        hits = await self._vector.search(collection, vector, top_k=top_k)
        return {"hits": [{"id": h.id, "score": h.score, "payload": h.payload} for h in hits]}

    async def _execute_trend_query(self, params: dict) -> dict:
        return await self._execute_search("market_intel", params["query"])

    async def _execute_competitor_analysis(self, params: dict) -> dict:
        return await self._execute_search("market_intel", params["query"])

    async def _execute_faq_search(self, params: dict) -> dict:
        return await self._execute_search("faq", params["query"])

    # —— capture:approval 动作现状快照(apply 漂移比对基准)——

    async def capture(self, action: str, params: dict) -> dict:
        handler = _CAPTURE_HANDLERS.get(action)
        if handler is None:
            raise ValueError(f"动作 {action} 无快照处理(非法审批动作)")
        async with SessionFactory() as session:
            return await handler(session, params)

    # —— apply:事务内执行已批动作 ——


async def _capture_product(session, params: dict) -> dict:
    product = await session.get(Product, params["product_id"])
    if product is None:
        return {"exists": False}
    return {"exists": True, "status": product.status.value, "title": product.title, "sku": product.sku}


async def _capture_price(session, params: dict) -> dict:
    product = await session.get(Product, params["product_id"])
    if product is None:
        return {"exists": False}
    return {"exists": True, "price": str(product.price), "title": product.title}


async def _capture_order(session, params: dict) -> dict:
    order = await session.get(Order, params["order_id"])
    if order is None:
        return {"exists": False}
    return {"exists": True, "status": order.status.value, "product_id": order.product_id}


_CAPTURE_HANDLERS: dict[str, Any] = {
    "product.publish": _capture_product,
    "product.unpublish": _capture_product,
    "product.delete": _capture_product,
    "product.update_price": _capture_price,
    "order.transition": _capture_order,
    "order.cancel": _capture_order,
}


async def apply_batch_actions(batch_id: str, actions: list[dict]) -> ApplyResult:
    """事务内执行一批已批/待补执行动作:全部成功才提交,任一漂移/非法整批回滚(批内同进同退,B18)。

    批次落 executed 与效果同事务(无中间窗口):durable 重放时批次已 executed → 幂等跳过,
    不会因重放再次执行或误报漂移冲突。状态为 shadow 的批次(演练补执行)同样可执行。
    """
    if not actions:
        return ApplyResult(applied=True)
    try:
        async with SessionFactory() as session, session.begin():
            row = (
                await session.execute(select(ApprovalBatch).where(ApprovalBatch.batch_id == batch_id))
            ).scalar_one_or_none()
            if row is None:
                raise ApplyConflict(f"批次 {batch_id} 不存在")
            if row.status == "executed":
                return ApplyResult(applied=True)  # durable 重放幂等
            if row.status not in ("approved", "shadow"):
                raise ApplyConflict(f"批次 {batch_id} 状态非法({row.status}),不可执行")
            await _lock_targets(session, actions)
            for item in actions:
                await _apply_one(session, item["action"], item["params"], item["snapshot"])
            row.status = ApprovalStatus.EXECUTED
            row.result = {"applied": True}
    except ApplyConflict as error:
        await _record_apply_failure(batch_id, str(error))
        return ApplyResult(applied=False, reason=str(error))
    return ApplyResult(applied=True)


async def _record_apply_failure(batch_id: str, reason: str) -> None:
    """冲突结果落批次 result 字段(审计),状态保持原值(approved/shadow)。"""
    async with SessionFactory() as session:
        row = (
            await session.execute(select(ApprovalBatch).where(ApprovalBatch.batch_id == batch_id))
        ).scalar_one_or_none()
        if row is None:
            return
        row.result = {"applied": False, "reason": reason}
        await session.commit()


def _lock_key(action: str) -> str:
    return "product" if action.startswith("product") else "order"


def _lock_id(action: str, params: dict) -> str:
    return str(params.get("product_id") or params.get("order_id"))


async def _lock_targets(session, actions: list[dict]) -> None:
    """行锁(串行化并发任务对同一行的操作),按 (表, id) 排序加锁防死锁。"""
    for item in sorted(actions, key=lambda a: (_lock_key(a["action"]), _lock_id(a["action"], a["params"]))):
        action = item["action"]
        if action.startswith("product"):
            await session.execute(
                select(Product.id).where(Product.id == item["params"]["product_id"]).with_for_update()
            )
        else:
            await session.execute(select(Order.id).where(Order.id == item["params"]["order_id"]).with_for_update())


async def _apply_one(session, action: str, params: dict, snapshot: dict) -> None:
    await _APPLY_HANDLERS[action](session, params, snapshot)


async def _locked_product(session, params: dict) -> Product:
    product = await session.get(Product, params["product_id"], with_for_update=True)
    if product is None:
        raise ApplyConflict(f"商品 {params['product_id']} 不存在或已删除")
    return product


async def _locked_order(session, params: dict) -> Order:
    order = await session.get(Order, params["order_id"], with_for_update=True)
    if order is None:
        raise ApplyConflict(f"订单 {params['order_id']} 不存在或已删除")
    return order


async def _apply_publish(session, params: dict, snapshot: dict) -> None:
    product = await _locked_product(session, params)
    if product.status.value != snapshot["status"]:
        raise ApplyConflict(
            f"商品 {params['product_id']} 状态已变化(快照 {snapshot['status']} → 当前 {product.status.value})"
        )
    product.status = ProductStatus.ACTIVE


async def _apply_unpublish(session, params: dict, snapshot: dict) -> None:
    product = await _locked_product(session, params)
    if product.status.value != snapshot["status"]:
        raise ApplyConflict(
            f"商品 {params['product_id']} 状态已变化(快照 {snapshot['status']} → 当前 {product.status.value})"
        )
    product.status = ProductStatus.INACTIVE


async def _apply_update_price(session, params: dict, snapshot: dict) -> None:
    product = await _locked_product(session, params)
    if str(product.price) != snapshot["price"]:
        raise ApplyConflict(f"商品 {params['product_id']} 价格已变化(快照 {snapshot['price']} → 当前 {product.price})")
    try:
        product.price = Decimal(str(params["new_price"]))
    except InvalidOperation as error:
        raise ApplyConflict(f"新价格非法:{params['new_price']!r}") from error


async def _apply_delete(session, params: dict, snapshot: dict) -> None:
    product = await _locked_product(session, params)
    if product.status.value != snapshot["status"]:
        raise ApplyConflict(
            f"商品 {params['product_id']} 状态已变化(快照 {snapshot['status']} → 当前 {product.status.value})"
        )
    refs = (
        await session.execute(select(func.count()).select_from(Order).where(Order.product_id == product.id))
    ).scalar_one()
    if refs > 0:
        raise ApplyConflict(f"商品 {params['product_id']} 存在 {refs} 个关联订单,不可删除")
    await session.delete(product)


async def _apply_transition(session, params: dict, snapshot: dict) -> None:
    order = await _locked_order(session, params)
    current = order.status
    if current.value != snapshot["status"]:
        raise ApplyConflict(f"订单 {params['order_id']} 状态已变化(快照 {snapshot['status']} → 当前 {current.value})")
    target = OrderStatus(params["to_status"])
    if target not in VALID_TRANSITIONS[current]:
        raise ApplyConflict(f"订单状态不可从 {current.value} 变更为 {target.value}")
    order.status = target


async def _apply_cancel(session, params: dict, snapshot: dict) -> None:
    order = await _locked_order(session, params)
    if order.status.value != snapshot["status"]:
        raise ApplyConflict(
            f"订单 {params['order_id']} 状态已变化(快照 {snapshot['status']} → 当前 {order.status.value})"
        )
    if order.status not in (OrderStatus.PENDING, OrderStatus.CONFIRMED):
        raise ApplyConflict(f"订单 {order.status.value} 态不可取消")
    order.status = OrderStatus.CANCELLED


_APPLY_HANDLERS: dict[str, Any] = {
    "product.publish": _apply_publish,
    "product.unpublish": _apply_unpublish,
    "product.update_price": _apply_update_price,
    "product.delete": _apply_delete,
    "order.transition": _apply_transition,
    "order.cancel": _apply_cancel,
}


# —— DB auto 工具(模块级:依赖 SessionFactory,不经 ToolExecutor 方法分发)——

_EXECUTE_HANDLERS: dict[str, Any] = {}


async def _execute_detect_anomalies(executor: ToolExecutor, params: dict) -> dict:
    keywords = ["退货", "退款", "投诉", "破损", "延迟", "丢失"]
    matched = [k for k in keywords if k in params["description"]]
    return {"anomaly": len(matched) > 0, "reason": f"订单包含异常关键词: {', '.join(matched)}" if matched else "正常"}


async def _execute_draft_create(executor: ToolExecutor, params: dict) -> dict:
    async with SessionFactory() as session:
        existing = (await session.execute(select(Product.id).where(Product.sku == params["sku"]))).scalar_one_or_none()
        if existing is not None:
            raise ValueError(f"SKU {params['sku']} 已存在")
        product = Product(
            sku=params["sku"],
            title=params["title"],
            price=Decimal(str(params["price"])),
            category=params["category"],
            description=params.get("description"),
            status=ProductStatus.DRAFT,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)
        return {"product_id": product.id, "sku": product.sku, "status": product.status.value}


async def _execute_draft_edit(executor: ToolExecutor, params: dict) -> dict:
    async with SessionFactory() as session:
        product = await session.get(Product, params["product_id"])
        if product is None:
            raise ValueError(f"商品 {params['product_id']} 未找到")
        if product.status != ProductStatus.DRAFT:
            raise ValueError(f"仅草稿可编辑(当前 {product.status.value})")
        if "title" in params:
            product.title = params["title"]
        if "price" in params:
            product.price = Decimal(str(params["price"]))
        if "category" in params:
            product.category = params["category"]
        if "description" in params:
            product.description = params["description"]
        await session.commit()
        return {
            "product_id": product.id,
            "title": product.title,
            "price": str(product.price),
            "category": product.category,
        }


async def _execute_list_orders(executor: ToolExecutor, params: dict) -> list[dict]:
    status = params.get("status")
    async with SessionFactory() as session:
        statement = select(Order).order_by(Order.created_at.desc())
        if status:
            statement = statement.where(Order.status == OrderStatus(status))
        rows = (await session.execute(statement)).scalars().all()
        results = []
        for order in rows:
            row = _order_to_dict(order)
            product = await session.get(Product, order.product_id)
            row["product"] = (
                {
                    "id": product.id,
                    "sku": product.sku,
                    "title": product.title,
                }
                if product
                else None
            )
            results.append(row)
        return results


def _order_to_dict(order: Order) -> dict:
    return {
        "id": order.id,
        "product_id": order.product_id,
        "customer_id": order.customer_id,
        "status": order.status.value,
        "total_amount": str(order.total_amount),
        "currency": order.currency,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


async def _execute_check_inventory(executor: ToolExecutor, params: dict) -> dict:
    """A7:库存检查读真实 stock 字段(五档告警),不再是 LLM 自报。"""
    async with SessionFactory() as session:
        product = await session.get(Product, params["product_id"])
        if product is None:
            raise ValueError(f"商品 {params['product_id']} 未找到")
        current_stock = product.stock
        title = product.title
    threshold = params["threshold"]
    if threshold <= 0:
        raise ValueError(f"库存阈值非法:{threshold!r}")
    ratio = current_stock / threshold
    if ratio <= 0:
        message = f"🔴 {title} 已售罄!请立即补货。"
    elif ratio < 0.3:
        message = f"🟠 {title} 库存严重不足 (当前: {current_stock}, 安全线: {threshold})。建议3天内补货。"
    elif ratio < 0.6:
        message = f"🟡 {title} 库存偏低 (当前: {current_stock}, 安全线: {threshold})。建议7天内补货。"
    elif ratio < 1:
        message = f"🔵 {title} 库存接近安全线 (当前: {current_stock})。关注销量趋势。"
    else:
        message = f"✅ {title} 库存充足 (当前: {current_stock})。"
    return {"alert": ratio < 1, "message": message, "current_stock": current_stock, "product_title": title}


async def _execute_list_approvals(executor: ToolExecutor, params: dict) -> list[dict]:
    status = params.get("status")
    async with SessionFactory() as session:
        statement = select(ApprovalBatch).order_by(ApprovalBatch.created_at.desc())
        if status:
            statement = statement.where(ApprovalBatch.status == status)
        rows = (await session.execute(statement)).scalars().all()
        return [
            {
                "batch_id": row.batch_id,
                "thread_id": row.thread_id,
                "slice_no": row.slice_no,
                "action_type": row.action_type,
                "actions": row.actions,
                "status": row.status,
                "mode": row.mode,
                "comment": row.comment,
            }
            for row in rows
        ]


async def _execute_order_lookup(executor: ToolExecutor, params: dict) -> dict | list[dict]:
    async with SessionFactory() as session:
        if "order_id" in params:
            order = await session.get(Order, params["order_id"])
            if order is None:
                raise ValueError(f"订单 {params['order_id']} 未找到")
            return _order_to_dict(order)
        if "customer_id" in params:
            rows = (
                (await session.execute(select(Order).where(Order.customer_id == params["customer_id"]))).scalars().all()
            )
            return [_order_to_dict(order) for order in rows]
        raise ValueError("order_lookup 需要 order_id 或 customer_id")


async def _execute_escalate_ticket(executor: ToolExecutor, params: dict) -> dict:
    async with SessionFactory() as session:
        ticket = Ticket(
            customer_id=params.get("customer_id"),
            message=params["message"],
            created_by="customer_service",
        )
        session.add(ticket)
        await session.commit()
        await session.refresh(ticket)
        return {"ticket_id": ticket.id, "status": ticket.status.value}


async def _execute_manage_template(executor: ToolExecutor, params: dict) -> dict:
    action = params["action"]
    scenario, locale = params.get("scenario"), params.get("locale")
    async with SessionFactory() as session:
        if action == "get":
            if not scenario or not locale:
                raise ValueError("manage_template get 需要 scenario 与 locale")
            row = (
                await session.execute(
                    select(ReplyTemplate).where(ReplyTemplate.scenario == scenario, ReplyTemplate.locale == locale)
                )
            ).scalar_one_or_none()
            if row is None:
                raise ValueError(f"模板不存在:{scenario}/{locale}")
            return {"id": row.id, "scenario": row.scenario, "locale": row.locale, "template": row.template}
        if action == "create":
            if not scenario or not locale or "template" not in params:
                raise ValueError("manage_template create 需要 scenario/locale/template")
            row = ReplyTemplate(id=str(uuid.uuid4()), scenario=scenario, template=params["template"], locale=locale)
            session.add(row)
            await session.commit()
            return {"id": row.id, "scenario": row.scenario, "locale": row.locale, "template": row.template}
        if action == "update":
            if not scenario or not locale or "template" not in params:
                raise ValueError("manage_template update 需要 scenario/locale/template")
            row = (
                await session.execute(
                    select(ReplyTemplate).where(ReplyTemplate.scenario == scenario, ReplyTemplate.locale == locale)
                )
            ).scalar_one_or_none()
            if row is None:
                raise ValueError(f"模板不存在:{scenario}/{locale}")
            row.template = params["template"]
            await session.commit()
            return {"id": row.id, "scenario": row.scenario, "locale": row.locale, "template": row.template}
        raise ValueError(f"manage_template 未知 action:{action}")


_EXECUTE_HANDLERS = {
    "trend_query": ToolExecutor._execute_trend_query,
    "competitor_analysis": ToolExecutor._execute_competitor_analysis,
    "scoring": ToolExecutor._execute_scoring,
    "generate_report": ToolExecutor._execute_generate_report,
    "draft.create": _execute_draft_create,
    "draft.edit": _execute_draft_edit,
    "list_orders": _execute_list_orders,
    "check_inventory": _execute_check_inventory,
    "detect_anomalies": _execute_detect_anomalies,
    "list_approvals": _execute_list_approvals,
    "faq_search": ToolExecutor._execute_faq_search,
    "order_lookup": _execute_order_lookup,
    "translate": ToolExecutor._execute_translate,
    "sentiment_analysis": ToolExecutor._execute_sentiment_analysis,
    "manage_template": _execute_manage_template,
    "escalate_ticket": _execute_escalate_ticket,
    "generate_draft": ToolExecutor._execute_generate_draft,
}
