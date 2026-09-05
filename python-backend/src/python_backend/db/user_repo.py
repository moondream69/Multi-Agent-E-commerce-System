"""系统用户表读写(认证用,小团队规模)。"""

from __future__ import annotations

from sqlalchemy import select

from python_backend.db.models import User
from python_backend.db.session import SessionLocal


def find_user_by_username(username: str) -> User | None:
    with SessionLocal() as session:
        return session.scalar(select(User).where(User.username == username))


def user_exists(username: str) -> bool:
    with SessionLocal() as session:
        return session.scalar(select(User.id).where(User.username == username)) is not None
