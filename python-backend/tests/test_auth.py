"""认证测试(spec #8 A1):JWT 签发/校验、登录流程、全门禁、管理员懒 seed。

- 单测:bcrypt 往返、token 签发/解码、过期拒绝
- 集成(真 PG):管理员 seed 幂等、登录 200/401、门禁 401/放行
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from python_backend.api.app import create_app
from python_backend.core.auth import (
    create_token,
    decode_token,
    ensure_admin_user,
    hash_password,
    verify_password,
)
from python_backend.db.models import User
from python_backend.db.session import SessionFactory
from python_backend.settings import get_settings
from tests.conftest import postgres_reachable

# —— 单测(离线) ——


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("s3cret")
    assert verify_password("s3cret", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("s3cret", "not-a-valid-bcrypt-hash")


def test_token_roundtrip_and_expiry() -> None:
    token = create_token("tester", 42)
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == "tester"
    assert payload["uid"] == 42

    # 过期 token → None(门禁 401 的数据基础)
    settings = get_settings()
    expired = jwt.encode(
        {
            "sub": "tester",
            "uid": 42,
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
        },
        settings.auth_jwt_secret,
        algorithm="HS256",
    )
    assert decode_token(expired) is None
    assert decode_token("garbage") is None


# —— 集成(真 PG,离线秒 skip) ——


@pytest.fixture(autouse=True)
def _require_postgres() -> None:
    if not postgres_reachable(get_settings().database_url):
        pytest.skip("Postgres 离线(compose dev 库),integration 跳过")


class _FakeSettings:
    """ensure_admin_user 只读两个字段:轻量假设置,不经 pydantic。"""

    auth_admin_username = "seed-admin"
    auth_admin_password = "seed-pw"


async def test_ensure_admin_user_idempotent(monkeypatch) -> None:
    """懒 seed 幂等:两次调用只落一行,密码可登录(凭据经注入,不依赖 .env)。"""
    monkeypatch.setattr("python_backend.core.auth.get_settings", lambda: _FakeSettings())
    await ensure_admin_user()
    await ensure_admin_user()
    async with SessionFactory() as session:
        rows = (
            (await session.execute(select(User).where(User.username == _FakeSettings.auth_admin_username)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert verify_password("seed-pw", rows[0].password_hash)


async def test_ensure_admin_user_skips_without_password(monkeypatch) -> None:
    """未配置凭据:跳过 seed(不开无密码后门),不访问 DB。"""

    class EmptyPasswordSettings:
        auth_admin_username = "admin"
        auth_admin_password = ""

    monkeypatch.setattr("python_backend.core.auth.get_settings", lambda: EmptyPasswordSettings())
    await ensure_admin_user()  # 不抛错即可(未配置凭据 → 跳过)


def _admin_user() -> tuple[str, str]:
    """测试专用用户(独立用户名,dev 库跨轮累积不冲突)。"""
    username = f"user-{uuid.uuid4().hex[:8]}"
    password = "pw-123456"
    return username, password


async def _seed_user(username: str, password: str) -> None:
    async with SessionFactory() as session, session.begin():
        session.add(User(username=username, password_hash=hash_password(password)))


def test_login_success_and_gating() -> None:
    """登录 200 签发 token;携 token 访问受保护端点放行;无/坏 token 401。"""
    username, password = _admin_user()
    asyncio.run(_seed_user(username, password))

    client = TestClient(create_app())  # 门禁默认开
    # 未认证 → 401
    assert client.get("/api/actions").status_code == 401
    assert client.get("/api/actions", headers={"Authorization": "Bearer bad"}).status_code == 401

    # 登录:错误凭据 401(不区分用户不存在/密码错)
    assert client.post("/api/auth/login", json={"username": username, "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "ghost", "password": "x"}).status_code == 401

    # 正确凭据 → token → 放行
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == username
    assert decode_token(body["token"]) is not None

    headers = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/actions", headers=headers).status_code == 200
