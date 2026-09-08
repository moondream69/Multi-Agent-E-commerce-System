"""Embedding → Milvus 灌库 → 检索全链路冒烟(e2e:需 Ollama 与 Milvus 在线)。

verify 阶段(`docker compose up -d` + Ollama bge-m3)执行;任一依赖不在线整体 skip。
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from urllib.parse import urlparse

import pytest
from pymilvus import MilvusClient

from python_backend.infrastructure.embedding import EmbeddingService
from python_backend.settings import get_settings
from python_backend.vector_repo.base import VectorRecord
from python_backend.vector_repo.milvus_repo import MilvusVectorRepository

pytestmark = pytest.mark.e2e

COLLECTION = "e2e_roundtrip"


def _milvus_reachable(uri: str, timeout: float = 2.0) -> bool:
    """TCP 快速探测:gRPC 连接失败的重试放大很慢,先探端口再构造客户端。"""
    parsed = urlparse(uri)
    try:
        with socket.create_connection((parsed.hostname or "", parsed.port or 19530), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def services() -> Iterator[tuple[EmbeddingService, MilvusVectorRepository]]:
    settings = get_settings()
    if not _milvus_reachable(settings.milvus_uri):
        pytest.skip("Milvus 不在线")
    try:
        client = MilvusClient(uri=settings.milvus_uri, timeout=5.0)
        client.has_collection("_connectivity_probe")
    except Exception as error:
        pytest.skip(f"Milvus 探活失败({type(error).__name__})")
    repo = MilvusVectorRepository(client=client)
    yield EmbeddingService(), repo
    if client.has_collection(COLLECTION):
        client.drop_collection(COLLECTION)


async def test_embed_upsert_search_roundtrip(services) -> None:
    embedding, repo = services
    try:
        vectors = await embedding.embed(["如何查询物流时效", "退货流程是什么"])
    except Exception as error:  # Ollama 不在线或模型未拉取 → 跳过而非失败
        pytest.skip(f"Ollama bge-m3 不可用({type(error).__name__})")

    await repo.upsert(
        COLLECTION,
        [
            VectorRecord(id="q1", vector=vectors[0], payload={"question": "如何查询物流时效"}),
            VectorRecord(id="q2", vector=vectors[1], payload={"question": "退货流程是什么"}),
        ],
    )
    hits = await repo.search(COLLECTION, vectors[0], top_k=2)
    assert hits[0].id == "q1"
    assert hits[0].payload == {"question": "如何查询物流时效"}
