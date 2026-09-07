"""健康检查:无数据库时以 degraded 呈现(骨架期仅验证入口与装配)。"""

from fastapi.testclient import TestClient

from python_backend.main import app


def test_health_returns_200() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert "db" in body["services"]
