"""向量检索单测:排序正确 + 阈值过滤(不依赖 Ollama,直接构造查询向量)。"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from python_backend.db.base import Base
from python_backend.db.models import FaqEmbedding
from python_backend.db.search import semantic_search
from python_backend.db.session import engine

DIM = 1024


def _one_hot(index: int) -> list[float]:
    vector = [0.0] * DIM
    vector[index] = 1.0
    return vector


@pytest.fixture()
def prepared_db():
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    yield


def test_search_sorts_by_similarity_and_filters_by_threshold(prepared_db):
    with Session(engine) as session:
        row_a = FaqEmbedding(id=uuid.uuid4(), question="退货", answer="退货说明", embedding=_one_hot(0))
        row_b = FaqEmbedding(id=uuid.uuid4(), question="物流", answer="物流说明", embedding=_one_hot(1))
        session.add_all([row_a, row_b])
        session.commit()

        # 库中可能有种子数据,限定只检索本用例插入的两行
        own_rows = FaqEmbedding.id.in_([row_a.id, row_b.id])

        try:
            results = semantic_search(session, FaqEmbedding, _one_hot(0), top_k=5, threshold=0.9, where=own_rows)

            # 与查询向量同向的 row_a 命中,且分数接近 1
            assert {r["question"] for r in results} == {"退货"}
            assert results[0]["score"] >= 0.99
            # 正交的 row_b 相似度为 0,被阈值过滤掉
            assert all(r["answer"] != "物流说明" for r in results)

            # 阈值放宽时两者都出现,且 a 排在 b 前
            permissive = semantic_search(session, FaqEmbedding, _one_hot(0), top_k=5, threshold=0.0, where=own_rows)
            assert [r["answer"] for r in permissive] == ["退货说明", "物流说明"]
        finally:
            session.execute(
                FaqEmbedding.__table__.delete().where(FaqEmbedding.__table__.c.id.in_([row_a.id, row_b.id]))
            )
            session.commit()
