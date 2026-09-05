"""10 张表模型:列名、索引、唯一约束与 NestJS 版(TypeORM)一一对应。

注意:TypeORM 未配置命名策略,列名是驼峰风格(如 createdAt、customerId、totalAmount),
数据库层面的列名必须保持原样——seed 数据与前端契约都依赖它。

第 10 张表 reply_templates(回复模板持久化)为迁移后新增,无 TypeORM 对应模型。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from python_backend.db.base import Base
from python_backend.settings import settings


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class AgentTaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SHADOW = "shadow"
    EXECUTED = "executed"


class User(Base):
    """第 11 张表:系统登录用户(小团队平权,无角色)。"""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    username: Mapped[str] = mapped_column(String(64), unique=True)
    passwordHash: Mapped[str] = mapped_column(String(255))
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Product(Base):
    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    sku: Mapped[str] = mapped_column(String(255), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    category: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(255), default="USD")
    platform: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(255), default="draft")
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    locale: Mapped[str] = mapped_column(String(255), default="zh-CN")
    preferences: Mapped[dict | None] = mapped_column(JSONB)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"))
    product: Mapped[Product] = relationship()
    customer_id: Mapped[UUID | None] = mapped_column(ForeignKey("customers.id"))
    customer: Mapped[Customer | None] = relationship()
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="orders_status_enum", values_callable=lambda e: [m.value for m in e]),
        default=OrderStatus.PENDING,
    )
    totalAmount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(255), default="USD")
    platform: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("idx_conversations_agent_id", "agentId"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    customerId: Mapped[str | None] = mapped_column(String(255))
    agentId: Mapped[str | None] = mapped_column(String(255))
    messages: Mapped[list] = mapped_column(JSONB)
    summary: Mapped[str | None] = mapped_column(Text)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        Index("idx_agent_tasks_agent_id", "agentId"),
        Index("idx_agent_tasks_correlation_id", "correlationId"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    agentId: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(255))
    status: Mapped[AgentTaskStatus] = mapped_column(
        Enum(
            AgentTaskStatus,
            name="agent_tasks_status_enum",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=AgentTaskStatus.PENDING,
    )
    input: Mapped[dict | None] = mapped_column(JSONB)
    output: Mapped[dict | None] = mapped_column(JSONB)
    correlationId: Mapped[str | None] = mapped_column(String(255))
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentMemory(Base):
    __tablename__ = "agent_memory"
    __table_args__ = (
        Index("idx_agent_memory_agent_id", "agentId"),
        UniqueConstraint("agentId", "key", name="uq_agent_memory_agent_id_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    agentId: Mapped[str] = mapped_column(String(255))
    key: Mapped[str] = mapped_column(String(255))
    value: Mapped[dict] = mapped_column(JSONB)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProductEmbedding(Base):
    __tablename__ = "product_embeddings"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    productId: Mapped[str] = mapped_column(String(255))
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dimension))
    content: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FaqEmbedding(Base):
    __tablename__ = "faq_embeddings"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dimension))
    locale: Mapped[str] = mapped_column(String(255), default="en")
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketEmbedding(Base):
    __tablename__ = "market_embeddings"

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    source: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dimension))
    category: Mapped[str] = mapped_column(String(255))
    collectedAt: Mapped[date | None] = mapped_column("collectedAt")
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReplyTemplate(Base):
    """第 10 张表:客服回复话术模板。id 为自然 slug(如 greeting),即工具契约中的 templateId;
    自然键 (scenario, locale) 唯一,seed 按此幂等跳过。"""

    __tablename__ = "reply_templates"
    __table_args__ = (UniqueConstraint("scenario", "locale", name="uq_reply_templates_scenario_locale"),)

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    scenario: Mapped[str] = mapped_column(String(255))
    template: Mapped[str] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(255), default="zh-CN")
    variables: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ApprovalRequest(Base):
    """第 12 张表:高危工具操作的人工审批/影子建议记录。

    mode='approval' 阻塞等待人工决定;mode='shadow' 只记录建议(影子模式),卖家一键补执行。
    """

    __tablename__ = "approval_requests"
    __table_args__ = (Index("idx_approval_status", "status"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    toolName: Mapped[str] = mapped_column(String(255))
    params: Mapped[dict] = mapped_column(JSONB)
    agentId: Mapped[str | None] = mapped_column(String(255))
    taskId: Mapped[str | None] = mapped_column(String(255))
    requestedBy: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(
            ApprovalStatus,
            name="approval_requests_status_enum",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=ApprovalStatus.PENDING,
    )
    mode: Mapped[str] = mapped_column(String(16), default="approval")
    result: Mapped[dict | None] = mapped_column(JSONB)
    decidedBy: Mapped[str | None] = mapped_column(String(255))
    decidedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    createdAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updatedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
