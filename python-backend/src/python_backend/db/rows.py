"""ORM 行 → JSON 安全的 dict(列名保持数据库原名,驼峰即驼峰;枚举/日期/Decimal/UUID 均转基础类型)。"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


def plain(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def row_to_dict(obj: Any) -> dict[str, Any]:
    return {attr.columns[0].name: plain(getattr(obj, attr.key)) for attr in obj.__mapper__.column_attrs}
