"""认证(spec #8 A1):JWT 登录 + 平权无角色 + 初始管理员懒 seed。

- bcrypt 哈希校验、pyjwt 签发(HS256;密钥/TTL 来自 settings,生产必改密钥)
- create_token/decode_token:签发与校验(token 过期/非法 → None)
- ensure_admin_user:启动时幂等 seed 管理员(auth_admin_password 留空则跳过——未配置凭据不开后门)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from sqlalchemy import select

from python_backend.db.models import User
from python_backend.db.session import SessionFactory
from python_backend.settings import get_settings

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:  # 哈希格式非法(库损坏/旧格式):视为不匹配,不抛 500
        return False


def create_token(username: str, user_id: int) -> str:
    """签发 JWT:sub=用户名、uid=用户 id、exp=TTL(settings.auth_token_ttl_hours)。"""
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": username,
        "uid": user_id,
        "iat": now,
        "exp": now + timedelta(hours=settings.auth_token_ttl_hours),
    }
    return jwt.encode(payload, settings.auth_jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    """校验并解出载荷:过期/签名非法/格式错误 → None(门禁 401)。"""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.auth_jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


async def ensure_admin_user() -> None:
    """初始管理员懒 seed(启动时调用,幂等):按用户名 upsert,不覆盖既有密码。"""
    settings = get_settings()
    if not settings.auth_admin_password:  # 未配置凭据:跳过(生产须显式设置)
        logger.warning("auth_admin_password 未配置,跳过管理员 seed(登录不可用)")
        return
    async with SessionFactory() as session, session.begin():
        existing = (
            await session.execute(select(User).where(User.username == settings.auth_admin_username))
        ).scalar_one_or_none()
        if existing is not None:
            return
        session.add(
            User(
                username=settings.auth_admin_username,
                password_hash=hash_password(settings.auth_admin_password),
            )
        )
