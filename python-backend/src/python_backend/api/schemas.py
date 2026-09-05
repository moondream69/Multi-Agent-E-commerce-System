"""入参校验(Pydantic,对应 NestJS 版缺失的 ValidationPipe——按 ADR 启用)。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateTaskDto(BaseModel):
    type: str = Field(min_length=1)
    input: dict[str, Any]
    targetAgentId: str | None = None


class ChatMessagePayload(BaseModel):
    text: str = Field(min_length=1)


class CreateOrderDto(BaseModel):
    """买家下单选入参(store 路由,模拟流量入口)。totalAmount 缺省取商品价格;customerEmail 缺省回落演示买家。"""

    productId: str = Field(min_length=1)
    totalAmount: float | None = None
    customerEmail: str | None = None
