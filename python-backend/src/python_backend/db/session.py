from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from python_backend.settings import settings

DATABASE_URL = (
    f"postgresql+psycopg://{settings.db_username}:{settings.db_password}"
    f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


def get_db() -> Generator[Session]:
    with SessionLocal() as session:
        yield session
