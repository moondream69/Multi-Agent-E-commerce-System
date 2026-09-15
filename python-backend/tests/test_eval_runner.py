"""缝 2(票 #58):``run`` 编排分支——注入假 HTTP(``httpx.MockTransport``)与假投影,断言
「发了什么、落了什么」;不触真网、不触库、不烧 token。

先例 = ``tests/test_simulator.py``(真 AsyncClient + 假传输)。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from python_backend.evals.corpus_anchor import CorpusAnchor
from python_backend.evals.runner import SEED_STEPS, SENTINEL_SKU, EvalRunner
from python_backend.evals.schema import Scenario
from python_backend.evals.snapshot import read_snapshot
from python_backend.infrastructure.tracing import task_trace_id

NOW = datetime(2026, 9, 15, 4, 0, tzinfo=UTC)
ANCHOR = CorpusAnchor(fingerprint="f" * 64, batch_id="batch-1")

SLICE_RESULT = {
    "agent": "product_research",
    "description": "检索市场情报并给出选品结论",
    "answer": "美国市场咖啡机需求上行 [1]",
    "executed": True,
    "citations": [{"number": 1, "doc_id": "usitc-digital-trade", "chunks": [{"id": "usitc-digital-trade#3"}]}],
}


class FakeProjection:
    """假投影:记录「发了什么」(先例 = tests/conftest.py 的 Recording* 形状)。"""

    def __init__(self, dataset_run_id: str | None = "ds-run-1") -> None:
        self.synced: list[list[Scenario]] = []
        self.runs: list[dict] = []
        self._dataset_run_id = dataset_run_id

    def sync_scenarios(self, scenarios: list[Scenario]) -> None:
        self.synced.append(list(scenarios))

    def record_run(self, *, run_name: str, scenario_id: str, trace_id: str, metadata: dict) -> str | None:
        self.runs.append({"run_name": run_name, "scenario_id": scenario_id, "trace_id": trace_id, "metadata": metadata})
        return self._dataset_run_id


def _scenario(scenario_id: str = "coffee-maker-us") -> Scenario:
    return Scenario(
        id=scenario_id, surface="选品报告", input="分析一下便携咖啡机在美国市场的选品机会", rubric=("评分等级",)
    )


def _handler(
    *,
    created: dict | None = None,
    detail: dict | None = None,
    products: list[dict] | None = None,
    login_status: int = 200,
    calls: dict[str, list] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    calls = calls if calls is not None else {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.setdefault(path, []).append(request)
        if path == "/api/auth/login":
            if login_status != 200:
                return httpx.Response(login_status, json={"detail": "凭据无效"})
            return httpx.Response(200, json={"token": "test-token", "username": "admin"})
        if path == "/api/products":
            return httpx.Response(200, json={"products": products if products is not None else []})
        if path == "/api/tasks":
            return httpx.Response(201, json=created or {"threadId": "t-1", "status": "completed"})
        if path.startswith("/api/tasks/"):
            return httpx.Response(200, json=detail or {"status": "completed", "results": {"1": SLICE_RESULT}})
        if path.startswith("/api/import/"):
            return httpx.Response(200, json={"report": {"created": 1, "skipped": 0, "errors": []}})
        raise AssertionError(f"未预期请求:{path}")

    return handler


def _runner(
    tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response], projection: FakeProjection | None = None
) -> tuple[EvalRunner, FakeProjection]:
    projection = projection or FakeProjection()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://eval")
    runner = EvalRunner(
        client,
        projection=projection,
        snapshot_dir=tmp_path / "runs",
        run_name="run-1",
        anchor=ANCHOR,
        now=lambda: NOW,
    )
    return runner, projection


async def test_run_scenario_writes_snapshot_and_projects(tmp_path: Path) -> None:
    """一条场景:POST 触发 → GET 读切片产出 → 快照落盘(run 目录/场景 id.json)→ 投影 run item。"""
    calls: dict[str, list] = {}
    runner, projection = _runner(tmp_path, _handler(calls=calls))
    await runner.login("admin", "pw")

    snapshots = await runner.run_scenarios([_scenario()])

    assert [snapshot.scenario_id for snapshot in snapshots] == ["coffee-maker-us"]
    assert projection.synced == [[_scenario()]]  # dataset 先就位,run item 才有落点
    assert json.loads(calls["/api/tasks"][0].content) == {
        "request": "分析一下便携咖啡机在美国市场的选品机会",
        "session_id": "eval-run-1",
    }

    path = tmp_path / "runs" / "run-1" / "coffee-maker-us.json"
    assert path.exists()
    snapshot = read_snapshot(path)
    assert snapshot.thread_id == "t-1"
    assert snapshot.trace_id == task_trace_id("t-1")  # 派生规则与既有观测同一条(同一函数,不重推导)
    assert snapshot.status == "completed"
    assert snapshot.corpus_fingerprint == "f" * 64
    assert snapshot.corpus_batch_id == "batch-1"
    assert snapshot.dataset_run_id == "ds-run-1"  # 投影回填(#59 的分数据此互链)
    assert snapshot.recorded_at == NOW
    assert [item.no for item in snapshot.slices] == [1]
    assert snapshot.slices[0].answer == "美国市场咖啡机需求上行 [1]"
    assert snapshot.slices[0].citations[0]["doc_id"] == "usitc-digital-trade"
    assert projection.runs == [
        {
            "run_name": "run-1",
            "scenario_id": "coffee-maker-us",
            "trace_id": task_trace_id("t-1"),
            "metadata": {
                "surface": "选品报告",
                "corpus_fingerprint": "f" * 64,
                "corpus_batch_id": "batch-1",
                "slice_count": 1,
            },
        }
    ]


async def test_run_scenario_keeps_snapshot_when_projection_fails(tmp_path: Path) -> None:
    """投影抛错 → 快照仍在盘上(任务已烧真 token,产出不能因投影抖动而丢),错误照抛。"""

    class BrokenProjection(FakeProjection):
        def record_run(self, **kwargs: object) -> str | None:
            raise RuntimeError("Langfuse 不可达")

    runner, _ = _runner(tmp_path, _handler(), projection=BrokenProjection())
    await runner.login("admin", "pw")

    with pytest.raises(RuntimeError, match="Langfuse 不可达"):
        await runner.run_scenarios([_scenario()])
    assert (tmp_path / "runs" / "run-1" / "coffee-maker-us.json").exists()


async def test_interrupted_scenario_fails_explicitly(tmp_path: Path) -> None:
    """遇 interrupt 显式报错、不自动批准(金标场景一律免审;挂起 = 场景设计缺陷)。"""
    runner, _ = _runner(tmp_path, _handler(created={"threadId": "t-1", "status": "interrupted"}))
    await runner.login("admin", "pw")

    with pytest.raises(RuntimeError, match="免审"):
        await runner.run_scenarios([_scenario()])
    assert not (tmp_path / "runs" / "run-1").exists()  # 无产出可评,不留半份快照


async def test_failed_scenario_fails_explicitly(tmp_path: Path) -> None:
    """任务失败同样显式报错(带图的错误原因)——跑不出产出的场景没有可评对象。"""
    runner, _ = _runner(tmp_path, _handler(created={"threadId": "t-2", "status": "failed", "error": "LLM 不可用"}))
    await runner.login("admin", "pw")

    with pytest.raises(RuntimeError, match="LLM 不可用"):
        await runner.run_scenarios([_scenario()])


async def test_scenario_without_slices_fails_explicitly(tmp_path: Path) -> None:
    """任务完成但 results 为空(无可评产出)→ 显式报错,不落一份空快照充数。"""
    runner, _ = _runner(tmp_path, _handler(detail={"status": "completed", "results": {}}))
    await runner.login("admin", "pw")

    with pytest.raises(RuntimeError, match="无切片产出"):
        await runner.run_scenarios([_scenario()])


async def test_check_clean_db_requires_sentinel(tmp_path: Path) -> None:
    """哨兵缺席 → 报错点名(防「忘了把 app 切到净库」把播种与跑批写进演示库)。"""
    runner, _ = _runner(tmp_path, _handler(products=[{"sku": "SYN-HM-001"}]))
    await runner.login("admin", "pw")
    with pytest.raises(RuntimeError, match=SENTINEL_SKU):
        await runner.check_clean_db()

    ok_runner, _ = _runner(tmp_path, _handler(products=[{"sku": SENTINEL_SKU}]))
    await ok_runner.login("admin", "pw")
    await ok_runner.check_clean_db()  # 哨兵在场即放行


async def test_seed_posts_three_csvs_in_order(tmp_path: Path) -> None:
    """播种走 REST(黑盒),顺序硬约束 商品→买家→订单;body = CSV 原文。"""
    csv_dir = tmp_path / "demo-data"
    csv_dir.mkdir()
    for _endpoint, name in SEED_STEPS:
        (csv_dir / name).write_text(f"# {name}\n", encoding="utf-8")

    calls: dict[str, list] = {}
    runner, _ = _runner(tmp_path, _handler(calls=calls))
    await runner.login("admin", "pw")

    reports = await runner.seed_synth_data(csv_dir)

    assert [endpoint for endpoint, _name in SEED_STEPS] == [
        "/api/import/products",
        "/api/import/customers",
        "/api/import/orders",
    ]
    # 调用序 = 播种序(请求按发生次序记录在 handler 里)
    assert [path for path in calls if path.startswith("/api/import/")] == [endpoint for endpoint, _name in SEED_STEPS]
    assert calls["/api/import/products"][0].content == b"# synth-products.csv\n"
    assert reports == [{"created": 1, "skipped": 0, "errors": []}] * 3


async def test_seed_rejects_missing_csv(tmp_path: Path) -> None:
    """合成数据缺席即报错点名(先跑 gen_synth_data.py),不静默跳过播种。"""
    runner, _ = _runner(tmp_path, _handler())
    with pytest.raises(RuntimeError, match="gen_synth_data"):
        await runner.seed_synth_data(tmp_path / "nowhere")


async def test_login_failure_is_explicit(tmp_path: Path) -> None:
    """登录失败 → 报错带状态码与响应片段(不带着匿名客户端往下跑)。"""
    runner, _ = _runner(tmp_path, _handler(login_status=401))
    with pytest.raises(RuntimeError, match="登录失败"):
        await runner.login("admin", "wrong")
