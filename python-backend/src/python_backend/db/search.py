"""pgvector 余弦相似度检索(手写 SQL,与 NestJS 版语义一致:相似度 = 1 - 余弦距离)。"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from python_backend.db.base import Base
from python_backend.db.rows import row_to_dict


def semantic_search(
    session: Session,
    model: type[Base],
    query_vector: list[float],
    *,
    top_k: int = 5,
    threshold: float = 0.0,
    where: Any | None = None,
) -> list[dict]:
    """按余弦相似度降序列出前 top_k 条,再按 threshold(相似度)过滤,返回 dict 列表(键为列名)。

    where 为 SQLAlchemy 条件表达式(如 FaqEmbedding.locale == "zh-CN"),可为 None。
    """
    embedding_col = model.__table__.c.embedding
    similarity = (1.0 - embedding_col.cosine_distance(query_vector)).label("score")

    stmt = select(model, similarity).order_by(similarity.desc()).limit(top_k)
    if where is not None:
        stmt = stmt.where(where)

    results: list[dict] = []
    for obj, score in session.execute(stmt).all():
        if float(score) < threshold:
            continue
        row = row_to_dict(obj)
        row["score"] = float(score)
        results.append(row)
    return results
