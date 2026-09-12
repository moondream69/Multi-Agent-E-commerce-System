"""前端静态托管(ADR-0005「部署」节):生产形态下单端口同源托管 dist 的路由语义。

离线可跑(不触库/不触外部服务):dist 树用 tmp_path 造,经 create_app 的 static_dir 注入位装配。
钉住四条语义:入口与资源可取、/api 不被遮蔽、无 dist 时基线不回归、缺 assets/ 目录不得炸。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import python_backend.main as main_module
from python_backend.api.app import create_app

INDEX_HTML = b"<!doctype html><div id=root></div>"
APP_JS = b"console.log('app');"


@pytest.fixture
def dist_dir(tmp_path: Path) -> Path:
    """最小 dist 树(多阶段构建产物的形状):index.html + assets/app.js。"""
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_bytes(INDEX_HTML)
    (assets / "app.js").write_bytes(APP_JS)
    return tmp_path


def test_root_serves_index_without_heuristic_cache(dist_dir: Path) -> None:
    """入口 200 且带 no-cache:重建后 hash 资源名变更,缓存旧入口会白屏。"""
    response = TestClient(create_app(static_dir=dist_dir)).get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.content == INDEX_HTML
    assert response.headers["cache-control"] == "no-cache"


def test_static_paths_bypass_auth_gate(dist_dir: Path) -> None:
    """门禁默认开,但只拦 /api 前缀:静态面照常可取(否则登录页本身都加载不了)。"""
    client = TestClient(create_app(static_dir=dist_dir))  # auth_required 默认 True
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 200


def test_asset_served_with_js_mime(dist_dir: Path) -> None:
    response = TestClient(create_app(static_dir=dist_dir)).get("/assets/app.js")
    assert response.status_code == 200
    assert response.content == APP_JS
    assert "javascript" in response.headers["content-type"]


def test_missing_asset_is_404_not_index(dist_dir: Path) -> None:
    """无 SPA 回退:资源缺失如实 404,不得回落 index.html。"""
    assert TestClient(create_app(static_dir=dist_dir)).get("/assets/nope.js").status_code == 404


def test_unknown_path_is_404(dist_dir: Path) -> None:
    """无 catch-all:任意未注册路径 404(加回退会连 /health 一起吞掉)。"""
    assert TestClient(create_app(static_dir=dist_dir)).get("/definitely-not-a-route").status_code == 404


def test_api_routes_not_shadowed(dist_dir: Path) -> None:
    """静态挂载不夺 /api:免鉴权时 200;门禁下 401(静态面产不出这两种响应形状)。"""
    open_client = TestClient(create_app(auth_required=False, static_dir=dist_dir))
    response = open_client.get("/api/actions")
    assert response.status_code == 200
    assert "actions" in response.json()

    assert TestClient(create_app(static_dir=dist_dir)).get("/api/approvals").status_code == 401


def test_late_registered_route_not_shadowed(dist_dir: Path) -> None:
    """模拟 main 时序:/health 在 create_app 返回后才注册,不得被吞(compose healthcheck 依赖)。"""
    app = create_app(static_dir=dist_dir)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_no_static_dir_keeps_baseline() -> None:
    """不传 static_dir(纯后端部署与既有离线套件):根路径如实 404,其余不变。"""
    client = TestClient(create_app())
    assert client.get("/").status_code == 404
    assert client.get("/assets/app.js").status_code == 404
    assert client.get("/docs").status_code == 200


def test_index_without_assets_dir_does_not_crash(tmp_path: Path) -> None:
    """守卫语义:StaticFiles 构造期即校验目录存在,缺 assets/ 时不得抛 RuntimeError。"""
    (tmp_path / "index.html").write_bytes(INDEX_HTML)
    client = TestClient(create_app(static_dir=tmp_path))
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 404


def test_resolve_static_dir_follows_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """dist 解析:镜像布局(有 /app/dist)得路径;本地 uv 开发(无 dist)得 None。"""
    monkeypatch.setattr(main_module, "__file__", str(tmp_path / "src" / "python_backend" / "main.py"))
    assert main_module._resolve_static_dir() is None
    (tmp_path / "dist").mkdir()
    assert main_module._resolve_static_dir() == tmp_path / "dist"
