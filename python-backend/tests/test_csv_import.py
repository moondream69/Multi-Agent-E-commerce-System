"""CSV 导入测试(spec #8 B8 + spec #11):解析纯函数单测(离线)+ 落库幂等集成(真 PG)。

- 解析:表头校验(缺必填/未知列)、行级容错(金额/库存/状态/币种/邮箱/偏好)、行号定位
- 落库:商品 sku 幂等跳过落 draft;订单 reference 幂等、SKU/邮箱解析、不扣库存;买家 email 幂等跳过
- 端点:text/csv 体 → 报告形状;编码错误 400;重传同文件结果稳定(幂等)
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from python_backend.api.app import create_app
from python_backend.core.imports import (
    CsvFormatError,
    import_customers,
    import_orders,
    import_products,
    parse_customers_csv,
    parse_orders_csv,
    parse_products_csv,
)
from python_backend.db.models import Customer, Order, Product, ProductStatus
from python_backend.db.session import SessionFactory
from python_backend.settings import get_settings
from tests.conftest import postgres_reachable

PRODUCTS_CSV = (
    "sku,title,price,category,currency,stock\nSKU-A,蓝牙音箱,19.99,数码,USD,10\nSKU-B,桌面支架,9.50,配件,USD,0\n"
)


def _products_csv() -> str:
    """每轮唯一 SKU 的测试 CSV(dev 库跨轮累积,固定 SKU 会跨测试互扰)。"""
    tag = uuid.uuid4().hex[:8]
    return (
        "sku,title,price,category,currency,stock\n"
        f"SKU-{tag}-A,蓝牙音箱,19.99,数码,USD,10\n"
        f"SKU-{tag}-B,桌面支架,9.50,配件,USD,0\n"
    )


def _orders_csv(sku_a: str, reference: str = "REF-1") -> str:
    return (
        "sku,total_amount,currency,status,reference,customer_email\n"
        f"{sku_a},19.99,USD,pending,{reference},buyer@example.com\n"
    )


# —— 解析纯函数单测(离线) ——


def test_parse_products_missing_required_header() -> None:
    with pytest.raises(CsvFormatError, match="缺必填列"):
        parse_products_csv("sku,title\nSKU-A,名字\n")


def test_parse_products_unknown_header() -> None:
    with pytest.raises(CsvFormatError, match="未知列"):
        parse_products_csv("sku,title,price,category,foo\nSKU-A,名字,1,数码,1\n")


def test_parse_products_row_level_errors_do_not_block_others() -> None:
    csv_text = (
        "sku,title,price,category,stock\n"
        "SKU-A,好商品,19.99,数码,10\n"  # 第 2 行:合法
        "SKU-B,坏价格,abc,数码,1\n"  # 第 3 行:价格非法
        "SKU-C,坏库存,5.00,数码,-2\n"  # 第 4 行:库存非法
        "SKU-D,坏币种,5.00,数码,1\n"  # 第 5 行:币种非法?币种缺省 USD,合法
    )
    rows, errors = parse_products_csv(csv_text)
    assert [r["sku"] for r in rows] == ["SKU-A", "SKU-D"]
    assert [e["row"] for e in errors] == [3, 4]
    assert rows[0]["price"] == Decimal("19.99")
    assert rows[0]["stock"] == 10
    assert rows[1]["currency"] == "USD"  # 缺省


def test_parse_products_alert_threshold_optional_column() -> None:
    """spec #9:alert_threshold 可选列——缺省取默认 10,非法值行级报错不阻断其他行。"""
    csv_text = (
        "sku,title,price,category,alert_threshold\n"
        "SKU-A,带阈值,19.99,数码,3\n"  # 第 2 行:显式阈值
        "SKU-B,缺省阈值,9.99,数码,\n"  # 第 3 行:空 → 默认
        "SKU-C,坏阈值,9.99,数码,abc\n"  # 第 4 行:非法
        "SKU-D,零阈值,9.99,数码,0\n"  # 第 5 行:非正 → 非法
    )
    rows, errors = parse_products_csv(csv_text)
    assert [r["sku"] for r in rows] == ["SKU-A", "SKU-B"]
    assert rows[0]["alert_threshold"] == 3
    assert rows[1]["alert_threshold"] == 10  # DEFAULT_ALERT_THRESHOLD
    assert [e["row"] for e in errors] == [4, 5]
    assert all("告警阈值非法" in e["reason"] for e in errors)


async def test_parse_orders_validation_and_defaults() -> None:
    rows, errors = parse_orders_csv(_orders_csv("SKU-A"))
    assert errors == []
    assert rows[0]["reference"] == "REF-1"
    assert rows[0]["status"] == "pending"
    assert rows[0]["total_amount"] == Decimal("19.99")

    bad, bad_errors = parse_orders_csv("sku,total_amount,status\nSKU-A,1,teleported\n")
    assert bad == []
    assert "状态非法" in bad_errors[0]["reason"]


# —— 落库集成(真 PG,离线秒 skip) ——


@pytest.fixture(autouse=True)
def _require_postgres() -> None:
    if not postgres_reachable(get_settings().database_url):
        pytest.skip("Postgres 离线(compose dev 库),integration 跳过")


async def test_import_products_creates_drafts_skips_existing() -> None:
    """商品导入:落 draft;sku 已存在跳过;重传同文件 → created 0、skipped 全部(幂等)。"""
    csv_text = _products_csv()
    rows, errors = parse_products_csv(csv_text)
    assert errors == []
    sku_a = rows[0]["sku"]
    report = await import_products(rows)
    assert report.created == 2 and report.skipped == 0

    async with SessionFactory() as session:
        product = (await session.execute(select(Product).where(Product.sku == sku_a))).scalar_one()
        assert product.status == ProductStatus.DRAFT
        assert product.stock == 10
        assert product.alert_threshold == 10  # 缺省阈值(spec #9)

    again = await import_products(rows)
    assert again.created == 0 and again.skipped == 2


async def test_import_orders_dedup_by_reference_and_resolves_sku() -> None:
    """订单导入:按 SKU 定位商品、reference 幂等;不扣库存、快照留空(历史数据入口)。"""
    product_rows, _ = parse_products_csv(_products_csv())
    await import_products(product_rows)
    sku_a = product_rows[0]["sku"]
    reference = f"REF-{uuid.uuid4().hex[:8]}"
    async with SessionFactory() as session:
        product = (await session.execute(select(Product).where(Product.sku == sku_a))).scalar_one()
        customer = (
            await session.execute(select(Customer).where(Customer.email == "buyer@example.com"))
        ).scalar_one_or_none()
        if customer is None:  # dev 库跨轮累积:同邮箱可能已存在(unique 约束)
            customer = Customer(name="测试买家", email="buyer@example.com")
            session.add(customer)
        await session.commit()
        stock_before = product.stock

    rows, errors = parse_orders_csv(_orders_csv(sku_a, reference=reference))
    assert errors == []
    report = await import_orders(rows)
    assert report.created == 1 and report.errors == []

    async with SessionFactory() as session:
        order = (await session.execute(select(Order).where(Order.reference == reference))).scalar_one()
        assert order.product_id == product.id
        assert order.fx_rate is None  # 快照留空(不经下单流程)
        product = await session.get(Product, product.id)
        assert product is not None
        assert product.stock == stock_before  # 历史导入不扣库存

    again = await import_orders(rows)
    assert again.created == 0 and again.skipped == 1  # reference 幂等


async def test_import_orders_row_errors_unknown_sku_and_email() -> None:
    """行级错误:未知 SKU / 未知买家邮箱 → errors 行级报告,不阻断其他行。"""
    product_rows, _ = parse_products_csv(_products_csv())
    await import_products(product_rows)
    csv_text = (
        "sku,total_amount,reference,customer_email\n"
        "SKU-NOPE,9.99,REF-X,\n"  # 未知 SKU
        f"{product_rows[0]['sku']},9.99,REF-Y,ghost@example.com\n"  # 未知邮箱
    )
    rows, _ = parse_orders_csv(csv_text)
    report = await import_orders(rows)
    assert report.created == 0
    assert {e["row"] for e in report.errors} == {2, 3}


async def test_import_orders_without_reference_all_created() -> None:
    """缺 reference 的行全部创建(不按 NULL 去重):同文件多行无 reference 全部落库。"""
    product_rows, _ = parse_products_csv(_products_csv())
    await import_products(product_rows)
    sku_a = product_rows[0]["sku"]
    csv_text = f"sku,total_amount\n{sku_a},9.99\n{sku_a},19.99\n"
    rows, _ = parse_orders_csv(csv_text)
    report = await import_orders(rows)
    assert report.created == 2  # 两行全部创建(修复:按 NULL 去重会只建第一条并崩溃)


# —— 买家入口(spec #11) ——


def test_parse_customers_validation_and_defaults() -> None:
    """买家解析:name/email 必填;locale/preferences 可选(偏好为 JSON 对象);行级容错不阻断。"""
    csv_text = (
        "name,email,locale,preferences\n"
        '张三,zhang@example.com,zh-CN,{"vip":true}\n'  # 第 2 行:全字段
        "李四,li@example.com,,\n"  # 第 3 行:locale/偏好缺省
        "王五,,zh-CN,\n"  # 第 4 行:缺 email
        "赵六,bad-email,zh-CN,\n"  # 第 5 行:email 非法
        "钱七,qi@example.com,zh-CN,not-json\n"  # 第 6 行:偏好非法
    )
    rows, errors = parse_customers_csv(csv_text)
    assert [r["email"] for r in rows] == ["zhang@example.com", "li@example.com"]
    assert rows[0]["preferences"] == {"vip": True}
    assert rows[1]["locale"] == "zh-CN"  # 缺省
    assert rows[1]["preferences"] == {}
    assert [e["row"] for e in errors] == [4, 5, 6]
    assert "邮箱非法" in errors[1]["reason"]
    assert "偏好非法" in errors[2]["reason"]


def _customers_csv() -> str:
    """每轮唯一邮箱的测试 CSV(dev 库跨轮累积,固定邮箱会跨测试互扰)。"""
    tag = uuid.uuid4().hex[:8]
    return f"name,email,locale\n买家甲,cust-{tag}-a@example.com,zh-CN\nBuyer B,cust-{tag}-b@example.com,\n"


async def test_import_customers_creates_skips_existing() -> None:
    """买家落库:email 幂等(已存在跳过);重传同文件 → created 0、skipped 全部。"""
    rows, errors = parse_customers_csv(_customers_csv())
    assert errors == []
    report = await import_customers(rows)
    assert report.created == 2 and report.skipped == 0

    async with SessionFactory() as session:
        customer = (await session.execute(select(Customer).where(Customer.email == rows[0]["email"]))).scalar_one()
        assert customer.name == rows[0]["name"]
        assert customer.locale == "zh-CN"

    again = await import_customers(rows)
    assert again.created == 0 and again.skipped == 2


# —— 端点(无 graph 装配即可,导入不依赖监督图) ——


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_app(auth_required=False)), base_url="http://test")


async def test_import_endpoint_report_shape() -> None:
    """POST /api/import/products(text/csv)→ 报告 {created, skipped, errors};编码错误 400。"""
    sku = f"SKU-{uuid.uuid4().hex[:8]}"
    csv_text = f"sku,title,price,category\n{sku},端点导入品,5.00,测试\n"
    async with _client() as client:
        response = await client.post("/api/import/products", content=csv_text, headers={"Content-Type": "text/csv"})
    assert response.status_code == 200
    assert set(response.json()["report"]) == {"created", "skipped", "errors"}
    assert response.json()["report"]["created"] == 1

    async with _client() as client:
        bad = await client.post(
            "/api/import/products", content=b"\xff\xfe\x00bad", headers={"Content-Type": "text/csv"}
        )
    assert bad.status_code == 400


async def test_import_customers_endpoint_report_shape() -> None:
    """POST /api/import/customers(text/csv)→ 报告 {created, skipped, errors}(与商品/订单同形)。"""
    tag = uuid.uuid4().hex[:8]
    csv_text = f"name,email\n端点买家,ep-{tag}@example.com\n"
    async with _client() as client:
        response = await client.post("/api/import/customers", content=csv_text, headers={"Content-Type": "text/csv"})
    assert response.status_code == 200
    assert set(response.json()["report"]) == {"created", "skipped", "errors"}
    assert response.json()["report"]["created"] == 1


async def test_import_customers_endpoint_rejects_bad_input() -> None:
    """买家导入入口:表头缺必填列与编码非法均 400 拒绝(与商品/订单导入同语义)。"""
    async with _client() as client:
        missing_column = await client.post(
            "/api/import/customers", content="name\n张三\n", headers={"Content-Type": "text/csv"}
        )
        bad_encoding = await client.post(
            "/api/import/customers", content=b"\xff\xfe\x00bad", headers={"Content-Type": "text/csv"}
        )
    assert missing_column.status_code == 400
    assert bad_encoding.status_code == 400
