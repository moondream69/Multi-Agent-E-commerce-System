"""认证:用户名密码登录 + JWT(Bearer)。

小团队平权:require_user 只验令牌签名/有效期,不查库(停用用户靠改 AUTH_JWT_SECRET 全员下线)。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from python_backend.db.user_repo import find_user_by_username
from python_backend.settings import settings

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_access_token(username: str) -> str:
    payload = {
        "sub": username,
        "exp": datetime.now(UTC) + timedelta(hours=settings.auth_token_ttl_hours),
    }
    return jwt.encode(payload, settings.auth_jwt_secret, algorithm="HS256")


def verify_token(token: str) -> str | None:
    """校验 JWT,返回用户名;无效/过期返回 None。"""
    try:
        payload = jwt.decode(token, settings.auth_jwt_secret, algorithms=["HS256"])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


class LoginDto(BaseModel):
    username: str
    password: str


def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),  # noqa: B008
) -> str:
    """认证依赖:校验 Authorization Bearer,把用户名写入 request.state.username。"""
    if credentials is None:
        raise HTTPException(status_code=401, detail="未认证")
    username = verify_token(credentials.credentials)
    if username is None:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    request.state.username = username
    return username


def build_auth_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/auth/login")
    def login(dto: LoginDto) -> dict:
        user = find_user_by_username(dto.username)
        if user is None or not verify_password(dto.password, user.passwordHash):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        return {"token": create_access_token(user.username), "username": user.username}

    @router.get("/api/auth/me")
    def me(username: str = Depends(require_user)) -> dict:
        return {"username": username}

    return router
