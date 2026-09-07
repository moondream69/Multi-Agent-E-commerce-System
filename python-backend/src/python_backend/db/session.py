"""数据库会话(与 Alembic 同源:env.py 复用本模块的 DATABASE_URL)。"""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from python_backend.settings import get_settings

DATABASE_URL = get_settings().database_url

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
