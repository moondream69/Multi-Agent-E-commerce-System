"""向量仓库包:接口 + Milvus 实现(术语表:VectorRepository)。"""

from python_backend.vector_repo.base import SearchHit, VectorRecord, VectorRepository
from python_backend.vector_repo.milvus_repo import MilvusVectorRepository

__all__ = ["MilvusVectorRepository", "SearchHit", "VectorRecord", "VectorRepository"]
