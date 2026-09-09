"""重构目标态业务表(宪章 ADR-0005)。

向量不落库(商品/FAQ/情报的 embedding 存 Milvus,经 VectorRepository 访问);
LangGraph checkpoint 表由 PostgresSaver.setup() 自行创建,不在 Alembic 内。
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from python_backend.db.base import Base


class OrderStatus(enum.StrEnum):
    """订单七态状态机:pending/confirmed 均可 → cancelled;cancelled/returned 为终态。"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    RETURNED = "returned"
    CANCELLED = "cancelled"


class ProductStatus(enum.StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"


# 库存告警阈值默认值(spec #9):迁移 0004 server_default、工具与 CSV 缺省共用同一常量
DEFAULT_ALERT_THRESHOLD = 10


class TaskStatus(enum.StrEnum):
    """任务状态:interrupted = 切片挂起等待人工环节(durable interrupt)。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalStatus(enum.StrEnum):
    """审批批次六态;mode 区分 approval(阻塞等待人工)与 shadow(演练只记录)。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SHADOW = "shadow"
    EXECUTED = "executed"


class TicketStatus(enum.StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class User(Base):
    """登录用户:小团队平权,无角色(宪章 A31)。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    """商品:stock 为真实库存(宪章 Q2,下单扣减、库存检查读库);对外状态变更进审批,草稿编辑免审。"""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    platform: Mapped[str] = mapped_column(String(20), default="amazon")
    category: Mapped[str] = mapped_column(String(50))
    status: Mapped[ProductStatus] = mapped_column(default=ProductStatus.DRAFT, server_default="draft")
    stock: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 库存告警阈值(商品级,spec #9 A9):扣减后低于该值发五档告警通知
    alert_threshold: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_ALERT_THRESHOLD, server_default=str(DEFAULT_ALERT_THRESHOLD)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Customer(Base):
    """买家(模拟数据对象,宪章 Q4):不接真实渠道,演练环境驱动闭环。"""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN")
    preferences: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Order(Base):
    """订单:fx_rate/fx_base_currency 为落库汇率快照(宪章 Q7,历史金额不随汇率漂移)。"""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reference: Mapped[str | None] = mapped_column(String(64))  # CSV 导入幂等去重键(可选,partial unique)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    status: Mapped[OrderStatus] = mapped_column(default=OrderStatus.PENDING, server_default="pending")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    fx_base_currency: Mapped[str] = mapped_column(String(3), default="CNY")
    platform: Mapped[str | None] = mapped_column(String(20))
    extra: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Conversation(Base):
    """会话(宪章 Q20):多会话 UI/历史隔离保留;LLM 上下文=短上下文+摘要,不做长期记忆。"""

    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("user_id", "session_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[str] = mapped_column(String(64), default="default")
    title: Mapped[str | None] = mapped_column(String(100))
    messages: Mapped[list] = mapped_column(JSONB, default=list)
    summary: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Task(Base):
    """任务(宪章 Q26):一次用户请求 = 一个 thread;slice_plan 为 Manager 的切片计划(含依赖声明)。"""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(40))
    status: Mapped[TaskStatus] = mapped_column(default=TaskStatus.PENDING, server_default="pending")
    slice_plan: Mapped[dict | None] = mapped_column(JSONB)
    input: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ApprovalBatch(Base):
    """审批批次(宪章 Q31):切片内同类型高危动作打包一张审批单,批内同进同退;actions 为参数快照(人类可读)。"""

    __tablename__ = "approval_batches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(36), unique=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    thread_id: Mapped[str] = mapped_column(String(64))
    slice_no: Mapped[int] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(String(40))
    actions: Mapped[list] = mapped_column(JSONB)
    status: Mapped[ApprovalStatus] = mapped_column(default=ApprovalStatus.PENDING, server_default="pending")
    mode: Mapped[str] = mapped_column(String(10), default="approval")
    requested_by: Mapped[str] = mapped_column(String(64))
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONB)
    run_output: Mapped[dict | None] = mapped_column(JSONB)  # 子图运行输出(spec #7:durable 重放的子图缓存)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Ticket(Base):
    """升级工单(宪章 Q3):escalate 落表,界面可见,升级不再是断头事件。"""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[TicketStatus] = mapped_column(default=TicketStatus.OPEN, server_default="open")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReplyTemplate(Base):
    """客服话术模板:按 场景+语言 唯一。"""

    __tablename__ = "reply_templates"
    __table_args__ = (UniqueConstraint("scenario", "locale"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario: Mapped[str] = mapped_column(String(40))
    template: Mapped[str] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(10))


class Faq(Base):
    """FAQ 知识库:embedding 存 Milvus(faq 集合)。"""

    __tablename__ = "faq"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(10), default="zh-CN")
    tags: Mapped[list] = mapped_column(ARRAY(String(40)), default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketIntel(Base):
    """市场情报:embedding 存 Milvus(market_intel 集合)。"""

    __tablename__ = "market_intel"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    content: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentTask(Base):
    """Agent 执行审计(宪章 A26):审批决定与 Langfuse trace 互链后,本表为落库审计源。"""

    __tablename__ = "agent_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    agent_id: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    input: Mapped[dict | None] = mapped_column(JSONB)
    output: Mapped[dict | None] = mapped_column(JSONB)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
