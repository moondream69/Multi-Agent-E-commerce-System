"""商品存储缝(spec #34):ProductStore 内存替身语义 + GET /api/products 流程(离线,不触 PG)。

本文件同时是 #9 遗留缝的回归面:端点在注入替身时不再直连 PG(探针数据只存在于替身,
响应可见 = 确实经 app.state.product_store 分发)。SQL 侧行为(排序表达式)的证明仍以
集成测试为准绳;此处保证端点接线与键形状可离线验证。
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from python_backend.api.app import create_app
from python_backend.db.models import Product
from tests.conftest import InMemoryProductStore


def _product(**overrides) -> Product:
    base = {
        "id": 1,
        "sku": "DAT-1",
        "title": "数据台商品",
        "price": Decimal("45.90"),
        "currency": "USD",
        "category": "厨房",
        "stock": 24,
        "alert_threshold": 10,
    }
    return Product(**{**base, **overrides})


# —— 内存替身可见语义 ——


async def test_list_products_returns_latest_first_and_contract_shape() -> None:
    """插入倒排 = 最新在前(生产 created_at 降序);键集与生产序列化同形。"""
    store = InMemoryProductStore()
    store.products.extend([_product(id=1, sku="DAT-1"), _product(id=2, sku="DAT-2")])

    products = await store.list_products()

    assert [item["id"] for item in products] == [2, 1]
    assert set(products[0]) == {
        "id",
        "sku",
        "title",
        "price",
        "currency",
        "category",
        "status",
        "stock",
        "alertThreshold",
    }
    assert products[0]["price"] == "45.90", "Decimal 序列化为字符串(不落浮点)"
    assert products[0]["alertThreshold"] == 10


async def test_empty_store_returns_empty_list() -> None:
    """空行集:不抛错,返回空列表(数据台显「无数据」由前端承担)。"""
    assert await InMemoryProductStore().list_products() == []


# —— 端点流程(注入替身) ——


def test_products_endpoint_serves_injected_store() -> None:
    """GET /api/products 经注入替身返回(离线反证:探针行只在替身里,零 PG 接触)。"""
    store = InMemoryProductStore()
    store.products.append(_product(id=7, sku="DAT-PROBE", title="替身探针"))

    response = TestClient(create_app(auth_required=False, product_store=store)).get("/api/products")

    assert response.status_code == 200
    assert [item["sku"] for item in response.json()["products"]] == ["DAT-PROBE"]
