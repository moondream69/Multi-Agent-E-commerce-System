"""VectorRepository 契约套件(非测试模块,由具体实现的测试文件注册)。

InMemory 参考实现验证套件本身(见 test_vector_repo_contract.py);
Milvus 真实现跑同一套件(见 test_milvus_repo.py,标 integration 需 docker Milvus 在线)。
向量统一 1024 维(settings.embedding_dimension)——Milvus 集合按真实维度建,低维向量会拒收。
"""

from __future__ import annotations

from python_backend.vector_repo.base import VectorRecord, VectorRepository


def vec(first: float = 1.0, second: float = 0.0) -> list[float]:
    """1024 维向量:仅前两维有区分度。"""
    v = [0.0] * 1024
    v[0] = first
    v[1] = second
    return v


# 每契约专属集合名:Milvus 实现下集合跨测试共享(module fixture),不自清理会交叉污染
CONTRACT_COLLECTIONS = [
    "contract_faq_upsert_search",
    "contract_faq_topk",
    "contract_products_filter",
    "contract_faq_overwrite",
    "contract_faq_delete",
]


async def contract_upsert_then_search_returns_hits_sorted_by_score(repo: VectorRepository) -> None:
    await repo.upsert(
        "contract_faq_upsert_search",
        [
            VectorRecord(id="f1", vector=vec(1.0, 0.0), payload={"locale": "zh-CN"}),
            VectorRecord(id="f2", vector=vec(0.0, 1.0), payload={"locale": "en-US"}),
        ],
    )
    hits = await repo.search("contract_faq_upsert_search", vec(1.0, 0.0), top_k=2)
    assert [h.id for h in hits] == ["f1", "f2"]
    assert hits[0].score > hits[1].score
    assert hits[0].payload == {"locale": "zh-CN"}


async def contract_search_respects_top_k(repo: VectorRepository) -> None:
    await repo.upsert(
        "contract_faq_topk",
        [VectorRecord(id=f"f{i}", vector=vec(1.0, i * 0.01)) for i in range(5)],
    )
    hits = await repo.search("contract_faq_topk", vec(1.0, 0.0), top_k=3)
    assert len(hits) == 3


async def contract_search_filter_narrows_results(repo: VectorRepository) -> None:
    await repo.upsert(
        "contract_products_filter",
        [
            VectorRecord(id="p1", vector=vec(1.0, 0.0), payload={"category": "电子"}),
            VectorRecord(id="p2", vector=vec(0.9, 0.1), payload={"category": "家居"}),
        ],
    )
    hits = await repo.search("contract_products_filter", vec(1.0, 0.0), top_k=5, filter='category == "家居"')
    assert [h.id for h in hits] == ["p2"]


async def contract_upsert_same_id_overwrites(repo: VectorRepository) -> None:
    await repo.upsert("contract_faq_overwrite", [VectorRecord(id="f1", vector=vec(1.0, 0.0), payload={"v": 1})])
    await repo.upsert("contract_faq_overwrite", [VectorRecord(id="f1", vector=vec(0.0, 1.0), payload={"v": 2})])
    hits = await repo.search("contract_faq_overwrite", vec(0.0, 1.0), top_k=1)
    assert hits[0].id == "f1"
    assert hits[0].payload == {"v": 2}


async def contract_delete_removes_records(repo: VectorRepository) -> None:
    await repo.upsert("contract_faq_delete", [VectorRecord(id="f1", vector=vec(1.0, 0.0))])
    await repo.delete("contract_faq_delete", ["f1", "不存在"])
    assert await repo.search("contract_faq_delete", vec(1.0, 0.0), top_k=1) == []
