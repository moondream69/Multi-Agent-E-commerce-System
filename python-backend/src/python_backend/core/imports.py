"""CSV 批量导入(spec #8 B8):商品/订单数据入口,行级容错 + 幂等语义。

- 解析与校验是纯函数(单测离线);落库走 SessionFactory(PG 集成测试)
- 幂等键:商品=sku(已存在跳过)、订单=reference(可选,已存在跳过;缺失的行全部创建)
- 商品落 draft(上架仍走审批护栏,三层分类不被导入破坏)
- 订单导入是历史数据入口:不扣库存(库存是现状)、不取汇率快照(fx_rate 留空)
- 报告 {created, skipped, errors}:errors 行号+原因,单行错误不阻断其他行
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from python_backend.db.models import Customer, Order, OrderStatus, Product, ProductStatus
from python_backend.db.session import SessionFactory


class CsvFormatError(Exception):
    """CSV 整体不可用(编码/表头/空文件):入口 400 拒绝,不落任何行。"""


@dataclass
class ImportReport:
    """行级导入报告:{created, skipped, errors:[{row, reason}]}。"""

    created: int = 0
    skipped: int = 0
    errors: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"created": self.created, "skipped": self.skipped, "errors": self.errors}


def _rows_from_csv(text: str, *, required: set[str], optional: set[str]) -> tuple[list[dict], list[dict]]:
    """解析 CSV 文本:表头校验 + 逐行必填/可选字段收集;返回 (合法行, 行级错误)。

    行号 = 文件数据行号(表头为第 1 行,首条数据为第 2 行)。
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise CsvFormatError("CSV 无表头")
    headers = {name.strip() for name in reader.fieldnames}
    known = required | optional
    if not required.issubset(headers):
        missing = sorted(required - headers)
        raise CsvFormatError(f"CSV 表头缺必填列:{', '.join(missing)}(现有:{', '.join(sorted(headers))})")
    unknown = headers - known
    if unknown:
        raise CsvFormatError(f"CSV 表头含未知列:{', '.join(sorted(unknown))}(合法列:{', '.join(sorted(known))})")

    rows: list[dict] = []
    errors: list[dict] = []
    for record in reader:
        row = {key: (record.get(key) or "").strip() for key in known}
        row_no = reader.line_num
        missing = [key for key in sorted(required) if not row[key]]
        if missing:
            errors.append({"row": row_no, "reason": f"缺必填值:{', '.join(missing)}"})
            continue
        rows.append({"row": row_no, "data": row})
    return rows, errors


def parse_products_csv(text: str) -> tuple[list[dict], list[dict]]:
    """商品 CSV 解析:sku/title/price/category 必填;currency/platform/stock/description 可选。

    行级校验:price>0(Decimal)、stock≥0 整数、currency 3 位字母。
    """
    rows, errors = _rows_from_csv(
        text,
        required={"sku", "title", "price", "category"},
        optional={"currency", "platform", "stock", "description"},
    )
    validated: list[dict] = []
    for entry in rows:
        row, data = entry["row"], entry["data"]
        try:
            price = Decimal(data["price"])
            if price <= 0:
                raise InvalidOperation
        except InvalidOperation:
            errors.append({"row": row, "reason": f"价格非法:{data['price']!r}"})
            continue
        stock = data.get("stock") or "0"
        try:
            stock_value = int(stock)
            if stock_value < 0:
                raise ValueError
        except ValueError:
            errors.append({"row": row, "reason": f"库存非法:{stock!r}(须为非负整数)"})
            continue
        currency = (data.get("currency") or "USD").upper()
        if len(currency) != 3 or not currency.isalpha():
            errors.append({"row": row, "reason": f"币种非法:{data.get('currency')!r}(ISO 3 位码)"})
            continue
        validated.append(
            {
                "row": row,
                "sku": data["sku"],
                "title": data["title"],
                "price": price,
                "category": data["category"],
                "currency": currency,
                "platform": data.get("platform") or "amazon",
                "stock": stock_value,
                "description": data.get("description") or None,
            }
        )
    return validated, errors


def parse_orders_csv(text: str) -> tuple[list[dict], list[dict]]:
    """订单 CSV 解析:sku/total_amount 必填;currency/status/customer_email/reference/platform 可选。

    订单按商品 SKU 定位(导入者不知道库内 ID);行级校验:金额>0、状态在七态内、币种 3 位。
    """
    rows, errors = _rows_from_csv(
        text,
        required={"sku", "total_amount"},
        optional={"currency", "status", "customer_email", "reference", "platform"},
    )
    valid_statuses = {s.value for s in OrderStatus}
    validated: list[dict] = []
    for entry in rows:
        row, data = entry["row"], entry["data"]
        try:
            amount = Decimal(data["total_amount"])
            if amount <= 0:
                raise InvalidOperation
        except InvalidOperation:
            errors.append({"row": row, "reason": f"金额非法:{data['total_amount']!r}"})
            continue
        currency = (data.get("currency") or "USD").upper()
        if len(currency) != 3 or not currency.isalpha():
            errors.append({"row": row, "reason": f"币种非法:{data.get('currency')!r}(ISO 3 位码)"})
            continue
        status = data.get("status") or OrderStatus.PENDING.value
        if status not in valid_statuses:
            errors.append({"row": row, "reason": f"状态非法:{status!r}(合法:{sorted(valid_statuses)})"})
            continue
        validated.append(
            {
                "row": row,
                "sku": data["sku"],
                "total_amount": amount,
                "currency": currency,
                "status": status,
                "customer_email": data.get("customer_email") or None,
                "reference": data.get("reference") or None,
                "platform": data.get("platform") or None,
            }
        )
    return validated, errors


async def import_products(rows: list[dict]) -> ImportReport:
    """商品落库:sku 已存在跳过(幂等),新建落 draft;不触发审批(直接入口,信任输入)。"""
    report = ImportReport()
    async with SessionFactory() as session, session.begin():
        for row in rows:
            existing = (await session.execute(select(Product.id).where(Product.sku == row["sku"]))).scalar_one_or_none()
            if existing is not None:
                report.skipped += 1
                continue
            session.add(
                Product(
                    sku=row["sku"],
                    title=row["title"],
                    price=row["price"],
                    category=row["category"],
                    currency=row["currency"],
                    platform=row["platform"],
                    stock=row["stock"],
                    description=row["description"],
                    status=ProductStatus.DRAFT,
                )
            )
            report.created += 1
    return report


async def import_orders(rows: list[dict]) -> ImportReport:
    """订单落库:reference 非空且已存在跳过(幂等);SKU 解析商品、email 解析买家,未知行级报错。

    历史数据入口:不扣库存(库存是现状)、不取汇率快照(fx_rate 留空)。
    缺 reference 的行不参与去重(全部创建,spec #8 US2)——不能按 NULL 查询去重:
    会命中库中既有无 reference 行,导致误跳过甚至多行匹配崩溃。
    """
    report = ImportReport()
    async with SessionFactory() as session, session.begin():
        for row in rows:
            reference = row["reference"]
            if reference:
                existing = (
                    await session.execute(select(Order.id).where(Order.reference == reference))
                ).scalar_one_or_none()
                if existing is not None:
                    report.skipped += 1
                    continue
            product_id = (
                await session.execute(select(Product.id).where(Product.sku == row["sku"]))
            ).scalar_one_or_none()
            if product_id is None:
                report.errors.append({"row": row["row"], "reason": f"SKU 不存在:{row['sku']}"})
                continue
            customer_id = None
            if row["customer_email"]:
                customer_id = (
                    await session.execute(select(Customer.id).where(Customer.email == row["customer_email"]))
                ).scalar_one_or_none()
                if customer_id is None:
                    report.errors.append({"row": row["row"], "reason": f"买家邮箱不存在:{row['customer_email']}"})
                    continue
            session.add(
                Order(
                    product_id=product_id,
                    customer_id=customer_id,
                    status=OrderStatus(row["status"]),
                    total_amount=row["total_amount"],
                    currency=row["currency"],
                    platform=row["platform"],
                    reference=row["reference"],
                )
            )
            report.created += 1
    return report
