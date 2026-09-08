"""Milvus 真实现契约测试(integration):需要 docker Milvus 在线(`docker compose up -d`)。

Milvus 不在线时整体 skip;在线时 MilvusVectorRepository 跑与 InMemory 相同的契约套件。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pymilvus import MilvusClient

from python_backend.settings import get_settings
from python_backend.vector_repo.base import VectorRepository
from python_backend.vector_repo.milvus_repo import MilvusVectorRepository
from tests.conftest import milvus_reachable
from tests.vector_repo_contracts import (
    CONTRACT_COLLECTIONS,
    contract_delete_removes_records,
    contract_search_filter_narrows_results,
    contract_search_respects_top_k,
    contract_upsert_same_id_overwrites,
    contract_upsert_then_search_returns_hits_sorted_by_score,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def repo() -> Iterator[VectorRepository]:
    if not milvus_reachable(get_settings().milvus_uri):
        pytest.skip("Milvus 不在线,跳过 integration 契约测试")
    try:
        client = MilvusClient(uri=get_settings().milvus_uri, timeout=5.0)
        client.has_collection("_connectivity_probe")
    except Exception as error:  # 端口通但服务异常 → 跳过而非失败
        pytest.skip(f"Milvus 探活失败({type(error).__name__})")
    repo = MilvusVectorRepository(client=client)
    yield repo
    for collection in CONTRACT_COLLECTIONS:
        if client.has_collection(collection):
            client.drop_collection(collection)


test_upsert_then_search_returns_hits_sorted_by_score = contract_upsert_then_search_returns_hits_sorted_by_score
test_search_respects_top_k = contract_search_respects_top_k
test_search_filter_narrows_results = contract_search_filter_narrows_results
test_upsert_same_id_overwrites = contract_upsert_same_id_overwrites
test_delete_removes_records = contract_delete_removes_records
