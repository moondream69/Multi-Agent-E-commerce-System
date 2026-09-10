"""健康检查:验证生产装配入口与 /health 响应形状(骨架期用例)。

依赖真实装配:python_backend.main 在导入期执行 build_app()(构造 MilvusVectorRepository),
且 TestClient 的 lifespan 会连 Postgres——故属 integration 面(CI 快速套件不跑,离线秒 skip)。
⚠️ main 的导入**必须留在测试体内**:模块级导入会在收集期触发 build_app(),即便本条被
deselect 也会因连不上 Milvus 而中断整个收集。
"""

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("requires_postgres")]


def test_health_returns_200() -> None:
    from python_backend.main import app

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert "db" in body["services"]
