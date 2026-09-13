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

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Protocol

from redis.asyncio import Redis
from sqlalchemy import func, select

from python_backend.agents.registry import REGISTRY
from python_backend.core.notifications import (
    EFFECT_FX_MISSING,
    EFFECT_INVENTORY_LOW,
    EFFECT_ORDER_STATUS,
    inventory_alert_message,
)
from python_backend.db.models import (
    DEFAULT_ALERT_THRESHOLD,
    ApprovalBatch,
    ApprovalStatus,
    Order,
    OrderStatus,
    Product,
    ProductStatus,
    ReplyTemplate,
    Ticket,
)
from python_backend.db.product_lookup import LOOKUP_MATCH_LIMIT, PostgresProductLookup, ProductLookupStore
from python_backend.db.session import SessionFactory
from python_backend.infrastructure.embedding import EmbeddingClient, EmbeddingService
from python_backend.infrastructure.fx import CNY, FxService, FxUnavailableError
from python_backend.infrastructure.llm import LlmClient, LlmService
from python_backend.settings import get_settings
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
    "order_create": "order.create",
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
    """apply 结果:effects 为效果描述(纯数据,spec #9)——由调用方在提交后组装通知。"""

    applied: bool
    reason: str | None = None
    effects: list[dict] = field(default_factory=list)


@dataclass
class OrderCreationResult:
    """下单结果(spec #9):订单载荷 + 效果描述(通知由入口在提交后组装)。"""

    order: dict
    effects: list[dict] = field(default_factory=list)


# apply 函数注入接缝(spec #7):生产 apply_batch_actions(事务+快照比对),测试注入假实现
ApplyFunction = Callable[[str, list[dict]], Awaitable[ApplyResult]]


class OrderCreationError(Exception):
    """下单参数非法(商品不存在/金额非法/汇率不可用包装层之外)。"""


class InsufficientStockError(OrderCreationError):
    """库存不足(负数防护):下单入口 409 如实报错;apply 侧包装为 ApplyConflict。"""


class FxProvider(Protocol):
    """汇率提供者协议(下单/apply 依赖的子集):测试注入假实现。"""

    async def get_rate_cny(self, currency: str) -> Decimal: ...


# 默认汇率服务:按事件循环惰性缓存(llm 并发闸同款模式,单进程模型下各 loop 独立)。
_fx_services: dict[asyncio.AbstractEventLoop, FxService] = {}


def default_fx() -> FxService:
    """默认汇率服务(真实 API + 设置中 Redis 缓存):apply 与下单入口的缺省实现。"""
    loop = asyncio.get_running_loop()
    service = _fx_services.get(loop)
    if service is None:
        # redis.asyncio.Redis 的 get 泛型与 FxCache 协议结构不完全对齐(运行时合法)
        service = _fx_services[loop] = FxService(
            redis=Redis(host=get_settings().redis_host, port=get_settings().redis_port)  # ty: ignore[invalid-argument-type]
        )
    return service


def _deduct_and_build_order(product: Product, params: dict, fx_rate: Decimal | None) -> Order:
    """「金额校验 → 库存校验 → 扣减 → Order 构造」共享核心(spec #8 B9:单一数据路径)。

    商品行须已由调用方加锁;校验失败抛 OrderCreationError/InsufficientStockError,
    由入口透传(409)或 apply 侧包装为 ApplyConflict(整批不执行)。
    fx_rate 为 None 表示汇率不可用(spec #9:留空落库 + 人工可见,不再拒单)。
    """
    try:
        amount = Decimal(str(params["total_amount"]))
    except InvalidOperation as error:
        raise OrderCreationError(f"订单金额非法:{params['total_amount']!r}") from error
    if amount <= 0:
        raise OrderCreationError(f"订单金额非法:{params['total_amount']!r}")
    if product.stock < 1:
        raise InsufficientStockError(f"商品 {product.id} 库存不足")
    product.stock -= 1
    return Order(
        product_id=product.id,
        customer_id=params.get("customer_id"),
        status=OrderStatus.PENDING,
        total_amount=amount,
        currency=params.get("currency") or "USD",
        fx_rate=fx_rate,
        fx_base_currency=CNY,
        platform=params.get("platform"),
        reference=params.get("reference"),
    )


def inventory_effect(product: Product) -> dict | None:
    """扣减后低于商品自身阈值 → 库存告警效果(spec #9 A9);未跌破 → None。

    共享核心与 apply 侧共用同一比对(两处不复制阈值逻辑)。
    """
    if product.stock < product.alert_threshold:
        return {
            "type": EFFECT_INVENTORY_LOW,
            "product_id": product.id,
            "title": product.title,
            "stock": product.stock,
            "threshold": product.alert_threshold,
        }
    return None


def _order_created_effects(order: Order, product: Product, *, fx_missing: bool) -> list[dict]:
    """订单创建的效果描述(下单两路共用):状态 pending + 汇率缺失(如有)+ 库存告警(如有)。"""
    effects: list[dict] = [
        {"type": EFFECT_ORDER_STATUS, "order_id": order.id, "from": None, "to": OrderStatus.PENDING.value}
    ]
    if fx_missing:
        effects.append({"type": EFFECT_FX_MISSING, "order_id": order.id})
    low = inventory_effect(product)
    if low is not None:
        effects.append(low)
    return effects


async def create_order_with_stock(
    *,
    product_id: int,
    total_amount: Decimal | str,
    currency: str = "USD",
    customer_id: int | None = None,
    platform: str | None = None,
    reference: str | None = None,
    fx_service: FxProvider | None = None,
) -> OrderCreationResult:
    """「创建订单 + 扣减库存」共享服务(spec #8 B9):REST 下单与直接入口共用同一数据路径。

    汇率快照在行锁外获取(网络调用不持锁);事务内行锁商品 → 库存校验(负数防护)
    → 扣减 → 订单落库(快照基准 CNY)。stock<1 抛 InsufficientStockError,由入口映射 409。
    汇率不可用(spec #9):不再拒单——fx_rate 留空落库 + fx_missing 效果(人工可见)。
    """
    fx_missing = False
    try:
        rate: Decimal | None = await (fx_service or default_fx()).get_rate_cny(currency)
    except FxUnavailableError:
        rate, fx_missing = None, True
    async with SessionFactory() as session, session.begin():
        product = await session.get(Product, product_id, with_for_update=True)
        if product is None:
            raise OrderCreationError(f"商品 {product_id} 不存在")
        params = {
            "total_amount": total_amount,
            "currency": currency,
            "customer_id": customer_id,
            "platform": platform,
            "reference": reference,
        }
        order = _deduct_and_build_order(product, params, rate)  # 与 apply 同一数据路径
        session.add(order)
        await session.flush()  # 取得自增 id(退出事务时提交)
        effects = _order_created_effects(order, product, fx_missing=fx_missing)
        return OrderCreationResult(order=_order_to_dict(order), effects=effects)


class Executor(Protocol):
    """工具执行器协议:子图依赖此协议而非具体实现(测试注入假执行器)。"""

    async def execute(self, action: str, params: dict) -> dict: ...

    async def capture(self, action: str, params: dict) -> dict: ...


class ToolExecutor:
    """真实执行器:DB 工具直连 SessionFactory,LLM/向量工具依赖注入的服务。"""

    def __init__(
        self,
        *,
        llm: LlmClient | None = None,
        vector: VectorRepository | None = None,
        embedding: EmbeddingClient | None = None,
        products: ProductLookupStore | None = None,
    ) -> None:
        self._llm = llm or LlmService()
        self._vector = vector
        self._embedding = embedding or EmbeddingService()
        # issue #35:product_lookup 数据源经此注入(默认 PG 实现;测试注入内存替身)
        self._products: ProductLookupStore = products or PostgresProductLookup()

    # —— execute:auto 动作直行(分发读动作注册表)——

    async def execute(self, action: str, params: dict) -> dict:
        spec = REGISTRY.get(action)
        if spec is None or spec.execute is None:
            raise ValueError(f"未知动作:{action}")
        return await spec.execute(self, params)

    async def _execute_translate(self, params: dict) -> dict:
        text, locale = params["text"], params["target_locale"]
        translated = await self._llm.complete(
            [
                {"role": "system", "content": f"你是专业翻译。请把用户文本翻译为 {locale},保持语气自然。"},
                {"role": "user", "content": text},
            ],
            temperature=0.3,
            max_tokens=2000,  # 思考模式推理与正文共享预算:低预算(原 500)会把正文饿成空,对齐默认档
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
            max_tokens=2000,  # 思考模式推理与正文共享预算:300 实测 ~1/3 概率正文为空(走查缺陷),对齐默认档
        )
        return json.loads(raw)

    async def _execute_generate_report(self, params: dict) -> dict:
        report = await self._llm.complete(
            [
                {"role": "system", "content": "你是选品分析师,请基于给定情报生成结构化选品分析报告(含结论与风险)。"},
                {"role": "user", "content": params.get("context", "")},
            ],
            max_tokens=8000,  # 思考模式推理与正文共享预算:报告类实测推理 3200~5700 字符,2000 连推理都装不下(空正文)
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
            max_tokens=2000,  # 思考模式推理与正文共享预算:10 必然被推理耗尽(空正文),对齐默认档
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

    # —— capture:approval 动作现状快照(apply 漂移比对基准;分发读动作注册表)——

    async def capture(self, action: str, params: dict) -> dict:
        spec = REGISTRY.get(action)
        if spec is None or spec.capture is None:
            raise ValueError(f"动作 {action} 无快照处理(非法审批动作)")
        async with SessionFactory() as session:
            return await spec.capture(session, params)

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


async def _capture_order_create(session, params: dict) -> dict:
    """order.create 快照:商品现状(stock 供人类可读,apply 只校验存在与库存充足性)。"""
    product = await session.get(Product, params["product_id"])
    if product is None:
        return {"exists": False}
    return {"exists": True, "stock": product.stock, "title": product.title, "price": str(product.price)}


async def apply_batch_actions(batch_id: str, actions: list[dict], *, fx: FxProvider | None = None) -> ApplyResult:
    """事务内执行一批已批/待补执行动作:全部成功才提交,任一漂移/非法整批回滚(批内同进同退,B18)。

    批次落 executed 与效果同事务(无中间窗口):durable 重放时批次已 executed → 幂等跳过,
    不会因重放再次执行或误报漂移冲突。状态为 shadow 的批次(演练补执行)同样可执行。
    order.create 的汇率快照在行锁外预取(网络不持锁);汇率不可用 → 留空落单 + fx_missing
    效果(spec #9:不再整批不执行)。effects 只在提交成功后返回(调用方据此组装通知)。
    """
    if not actions:
        return ApplyResult(applied=True)
    try:
        fx_rates = await _prefetch_fx_rates(actions, fx)
    except FxUnavailableError as error:
        await _record_apply_failure(batch_id, str(error))
        return ApplyResult(applied=False, reason=str(error))
    effects: list[dict] = []
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
            for index, item in enumerate(actions):
                effects.extend(
                    await _apply_one(session, item["action"], item["params"], item["snapshot"], fx_rates[index])
                )
            row.status = ApprovalStatus.EXECUTED
            row.result = {"applied": True}
    except ApplyConflict as error:
        await _record_apply_failure(batch_id, str(error))
        return ApplyResult(applied=False, reason=str(error))
    return ApplyResult(applied=True, effects=effects)


async def _prefetch_fx_rates(actions: list[dict], fx: FxProvider | None) -> list[Decimal | None]:
    """order.create 动作的汇率快照预取(行锁外):非该动作占位 None,索引与 actions 对齐。

    单动作汇率不可用 → 该动作 None(spec #9:留空落单 + fx_missing 效果,不拖垮整批)。
    """
    rates: list[Decimal | None] = [None] * len(actions)
    if not any(item["action"] == "order.create" for item in actions):
        return rates
    service = fx or default_fx()
    for index, item in enumerate(actions):
        if item["action"] == "order.create":
            try:
                rates[index] = await service.get_rate_cny(item["params"].get("currency") or "USD")
            except FxUnavailableError:
                rates[index] = None  # 留空:apply 侧记 fx_missing 效果
    return rates


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
    # order.create 扣商品库存,锁目标是商品行(与订单流转/取消锁订单行不同)
    return "product" if action.startswith("product") or action == "order.create" else "order"


def _lock_id(action: str, params: dict) -> str:
    return str(params.get("product_id") or params.get("order_id"))


async def _lock_targets(session, actions: list[dict]) -> None:
    """行锁(串行化并发任务对同一行的操作),按 (表, id) 排序加锁防死锁。"""
    for item in sorted(actions, key=lambda a: (_lock_key(a["action"]), _lock_id(a["action"], a["params"]))):
        action = item["action"]
        if action.startswith("product") or action == "order.create":
            await session.execute(
                select(Product.id).where(Product.id == item["params"]["product_id"]).with_for_update()
            )
        else:
            await session.execute(select(Order.id).where(Order.id == item["params"]["order_id"]).with_for_update())


async def _apply_one(session, action: str, params: dict, snapshot: dict, fx_rate: Decimal | None = None) -> list[dict]:
    spec = REGISTRY.get(action)
    if spec is None or spec.apply is None:
        raise ApplyConflict(f"动作 {action} 无 apply 处理(非法审批动作)")
    effects = await spec.apply(session, params, snapshot, fx_rate)  # 非 order.create 处理器忽略该参数
    return effects or []  # 效果描述(spec #9):无效果的处理器返回 None


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


async def _apply_publish(session, params: dict, snapshot: dict, fx_rate: Decimal | None = None) -> None:
    product = await _locked_product(session, params)
    if product.status.value != snapshot["status"]:
        raise ApplyConflict(
            f"商品 {params['product_id']} 状态已变化(快照 {snapshot['status']} → 当前 {product.status.value})"
        )
    product.status = ProductStatus.ACTIVE


async def _apply_unpublish(session, params: dict, snapshot: dict, fx_rate: Decimal | None = None) -> None:
    product = await _locked_product(session, params)
    if product.status.value != snapshot["status"]:
        raise ApplyConflict(
            f"商品 {params['product_id']} 状态已变化(快照 {snapshot['status']} → 当前 {product.status.value})"
        )
    product.status = ProductStatus.INACTIVE


async def _apply_update_price(session, params: dict, snapshot: dict, fx_rate: Decimal | None = None) -> None:
    product = await _locked_product(session, params)
    if str(product.price) != snapshot["price"]:
        raise ApplyConflict(f"商品 {params['product_id']} 价格已变化(快照 {snapshot['price']} → 当前 {product.price})")
    try:
        product.price = Decimal(str(params["new_price"]))
    except InvalidOperation as error:
        raise ApplyConflict(f"新价格非法:{params['new_price']!r}") from error


async def _apply_delete(session, params: dict, snapshot: dict, fx_rate: Decimal | None = None) -> None:
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


async def _apply_transition(session, params: dict, snapshot: dict, fx_rate: Decimal | None = None) -> list[dict]:
    order = await _locked_order(session, params)
    current = order.status
    if current.value != snapshot["status"]:
        raise ApplyConflict(f"订单 {params['order_id']} 状态已变化(快照 {snapshot['status']} → 当前 {current.value})")
    target = OrderStatus(params["to_status"])
    if target not in VALID_TRANSITIONS[current]:
        raise ApplyConflict(f"订单状态不可从 {current.value} 变更为 {target.value}")
    order.status = target
    return [{"type": EFFECT_ORDER_STATUS, "order_id": order.id, "from": current.value, "to": target.value}]


async def _apply_cancel(session, params: dict, snapshot: dict, fx_rate: Decimal | None = None) -> list[dict]:
    order = await _locked_order(session, params)
    if order.status.value != snapshot["status"]:
        raise ApplyConflict(
            f"订单 {params['order_id']} 状态已变化(快照 {snapshot['status']} → 当前 {order.status.value})"
        )
    if order.status not in (OrderStatus.PENDING, OrderStatus.CONFIRMED):
        raise ApplyConflict(f"订单 {order.status.value} 态不可取消")
    previous = order.status
    order.status = OrderStatus.CANCELLED
    return [
        {"type": EFFECT_ORDER_STATUS, "order_id": order.id, "from": previous.value, "to": OrderStatus.CANCELLED.value}
    ]


async def _apply_order_create(session, params: dict, snapshot: dict, fx_rate: Decimal | None) -> list[dict]:
    """order.create 效果后置执行:行锁商品 → 快照比对(库存漂移=B18 冲突)→ 共享核心扣减落单。

    与 REST 下单共享 _deduct_and_build_order(spec #8:单一数据路径,不三处复制);
    领域校验错误包装为 ApplyConflict(批内同进同退,整批不执行)。
    汇率不可用(fx_rate None,spec #9)→ 留空落单 + fx_missing 效果,不再整批不执行。
    """
    product = await _locked_product(session, params)  # 商品不存在 → ApplyConflict
    if product.stock != snapshot.get("stock"):
        raise ApplyConflict(
            f"商品 {params['product_id']} 库存已变化(快照 {snapshot.get('stock')} → 当前 {product.stock})"
        )
    try:
        order = _deduct_and_build_order(product, params, fx_rate)
    except (OrderCreationError, InsufficientStockError) as error:
        raise ApplyConflict(str(error)) from error
    session.add(order)
    await session.flush()  # 取 id 供效果描述(事务内,回滚则效果一并丢弃)
    return _order_created_effects(order, product, fx_missing=fx_rate is None)


# —— 动作注册表(spec #8:分类/capture/apply/前端标签一处维护;labels 供 GET /api/actions 渲染)——

# 审批动作(一切对外状态变更):capture 快照 + apply 事务执行
REGISTRY.register("product.publish", risk="approval", label="上架商品", capture=_capture_product, apply=_apply_publish)
REGISTRY.register(
    "product.unpublish", risk="approval", label="下架商品", capture=_capture_product, apply=_apply_unpublish
)
REGISTRY.register(
    "product.update_price", risk="approval", label="修改价格", capture=_capture_price, apply=_apply_update_price
)
REGISTRY.register("product.delete", risk="approval", label="删除商品", capture=_capture_product, apply=_apply_delete)
REGISTRY.register(
    "order.transition", risk="approval", label="订单流转", capture=_capture_order, apply=_apply_transition
)
REGISTRY.register("order.cancel", risk="approval", label="取消订单", capture=_capture_order, apply=_apply_cancel)
REGISTRY.register(
    "order.create", risk="approval", label="创建订单", capture=_capture_order_create, apply=_apply_order_create
)


# —— DB auto 工具(模块级:依赖 SessionFactory,不经 ToolExecutor 方法分发)——


async def _execute_detect_anomalies(executor: ToolExecutor, params: dict) -> dict:
    keywords = ["退货", "退款", "投诉", "破损", "延迟", "丢失"]
    matched = [k for k in keywords if k in params["description"]]
    return {"anomaly": len(matched) > 0, "reason": f"订单包含异常关键词: {', '.join(matched)}" if matched else "正常"}


def _positive_threshold(value) -> int | None:
    """库存告警阈值校验(spec #9):正整数;None/缺省 → None(调用方取默认 10)。"""
    if value is None:
        return None
    try:
        threshold = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"库存告警阈值非法:{value!r}") from error
    if threshold <= 0:
        raise ValueError(f"库存告警阈值须为正整数:{value!r}")
    return threshold


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
            alert_threshold=_positive_threshold(params.get("alert_threshold")) or DEFAULT_ALERT_THRESHOLD,
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
        if "alert_threshold" in params:
            threshold = _positive_threshold(params["alert_threshold"])
            if threshold is None:
                raise ValueError("库存告警阈值不可为空")
            product.alert_threshold = threshold
        await session.commit()
        return {
            "product_id": product.id,
            "title": product.title,
            "price": str(product.price),
            "category": product.category,
            "alert_threshold": product.alert_threshold,
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
        "reference": order.reference,
        "product_id": order.product_id,
        "customer_id": order.customer_id,
        "status": order.status.value,
        "total_amount": str(order.total_amount),
        "currency": order.currency,
        "fx_rate": str(order.fx_rate) if order.fx_rate is not None else None,
        "fx_base_currency": order.fx_base_currency,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


async def _execute_check_inventory(executor: ToolExecutor, params: dict) -> dict:
    """A7/A9:库存检查读真实 stock 字段(五档告警),不再是 LLM 自报。

    阈值缺省读商品自身 alert_threshold(spec #9:消除 LLM 自报阈值残余);传入时以传入值为准。
    """
    async with SessionFactory() as session:
        product = await session.get(Product, params["product_id"])
        if product is None:
            raise ValueError(f"商品 {params['product_id']} 未找到")
        current_stock = product.stock
        title = product.title
        # 缺省读商品阈值;显式传值(含非法 0/负数)以传入值为准并如实报错(不静默替换)
        threshold = params["threshold"] if params.get("threshold") is not None else product.alert_threshold
    if threshold <= 0:
        raise ValueError(f"库存阈值非法:{threshold!r}")
    return {
        "alert": current_stock < threshold,
        "message": inventory_alert_message(title, current_stock, threshold),
        "current_stock": current_stock,
        "product_title": title,
        "threshold": threshold,
    }


async def _execute_product_lookup(executor: ToolExecutor, params: dict) -> dict:
    """issue #35:按 SKU/标题/类目定位商品(只读),把口语指代解析成 ID 供后续动作。

    入参校验(至少给一、sku 优先)与截断语义在存储层 lookup 内统一(生产/替身同口径);
    issue #42:category 分支支撑「按类目盘货」问法(如「宠物用品类目有哪些商品」)。
    """
    hits, truncated = await executor._products.lookup(
        sku=params.get("sku"),
        title=params.get("title"),
        category=params.get("category"),
        limit=LOOKUP_MATCH_LIMIT,
    )
    return {"matches": hits, "truncated": truncated}


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


# 免审动作(draft 内部编辑 + 只读/纯函数工具):execute 直行
REGISTRY.register("trend_query", risk="auto", label="市场趋势查询", execute=ToolExecutor._execute_trend_query)
REGISTRY.register(
    "competitor_analysis", risk="auto", label="竞品分析", execute=ToolExecutor._execute_competitor_analysis
)
REGISTRY.register("scoring", risk="auto", label="选品评分", execute=ToolExecutor._execute_scoring)
REGISTRY.register("generate_report", risk="auto", label="生成选品报告", execute=ToolExecutor._execute_generate_report)
REGISTRY.register("draft.create", risk="auto", label="创建商品草稿", execute=_execute_draft_create)
REGISTRY.register("draft.edit", risk="auto", label="编辑商品草稿", execute=_execute_draft_edit)
REGISTRY.register("list_orders", risk="auto", label="订单列表", execute=_execute_list_orders)
REGISTRY.register("check_inventory", risk="auto", label="库存检查", execute=_execute_check_inventory)
REGISTRY.register("product_lookup", risk="auto", label="商品查询", execute=_execute_product_lookup)
REGISTRY.register("detect_anomalies", risk="auto", label="异常检测", execute=_execute_detect_anomalies)
REGISTRY.register("list_approvals", risk="auto", label="审批批次列表", execute=_execute_list_approvals)
REGISTRY.register("faq_search", risk="auto", label="FAQ 检索", execute=ToolExecutor._execute_faq_search)
REGISTRY.register("order_lookup", risk="auto", label="订单查询", execute=_execute_order_lookup)
REGISTRY.register("translate", risk="auto", label="翻译", execute=ToolExecutor._execute_translate)
REGISTRY.register("sentiment_analysis", risk="auto", label="情感分析", execute=ToolExecutor._execute_sentiment_analysis)
REGISTRY.register("manage_template", risk="auto", label="回复模板管理", execute=_execute_manage_template)
REGISTRY.register("escalate_ticket", risk="auto", label="升级工单", execute=_execute_escalate_ticket)
REGISTRY.register("generate_draft", risk="auto", label="生成回复草稿", execute=ToolExecutor._execute_generate_draft)
