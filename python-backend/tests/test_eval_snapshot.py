"""缝 1(票 #58):快照读写往返与 schema 断言——离线纯逻辑,不触网不触库。

先例 = ``tests/test_eval_scenarios.py``(评测包公开函数的逐字段断言)。
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from python_backend.evals.snapshot import (
    SNAPSHOT_VERSION,
    SliceOutput,
    Snapshot,
    plan_lines,
    read_snapshot,
    slices_from_plan,
    slices_from_results,
    snapshot_path,
    write_snapshot,
)

RECORDED_AT = datetime(2026, 9, 15, 3, 30, tzinfo=UTC)

CITATIONS = [
    {
        "number": 1,
        "kind": "corpus",
        "doc_id": "usitc-digital-trade",
        "title": "Global Digital Trade",
        "source": "USITC(公有领域)",
        "published_at": "2024-05-01",
        "chunks": [{"id": "usitc-digital-trade#3", "score": 0.71, "section": "3", "chunk_index": 3, "content": "…"}],
    }
]

# 查证证据块(#67;起草工作台线端点随草稿返回的那份,原样落快照——判据②③的核验面)
EVIDENCE = {
    "faq_hits": [{"id": "faq-logistics#1", "score": 0.56, "payload": {"content": "Q: 下单后多久发货?"}}],
    "order": {"id": 1042, "status": "shipped", "total_amount": "299.00", "currency": "CNY", "product": None},
    "order_id": 1042,
    "products": [
        {
            "id": 82,
            "sku": "SYN-HM-081",
            "title": "桌面收纳架 深空黑款",
            "price": "129.00",
            "currency": "CNY",
            "category": "家居",
            "status": "draft",
            "stock": 2,
        }
    ],
    "products_truncated": False,
}

# GET /api/tasks/{thread_id} 的 plan 段(切片计划载荷,票 #61)
PLAN = {
    "slices": [
        {"no": 2, "agent": "product_research", "description": "评分", "depends_on": [1], "approval_points": []},
        {
            "no": 1,
            "agent": "product_research",
            "description": "检索市场情报",
            "depends_on": [],
            "approval_points": ["上架"],
        },
    ]
}


def _snapshot() -> Snapshot:
    return Snapshot(
        scenario_id="coffee-maker-us",
        surface="选品报告",
        run_name="run-20260915T033000Z",
        thread_id="t-1",
        trace_id="a3f1c2d4e5b60718293a4b5c6d7e8f90",
        status="completed",
        slices=(
            SliceOutput(
                no=1,
                agent="product_research",
                description="检索市场情报并给结论",
                answer="美国市场咖啡机需求上行 [1]",
                citations=tuple(CITATIONS),
                executed=True,
                evidence=EVIDENCE,  # #67:工作台线切片的查证证据块(任务线为 None)
            ),
            SliceOutput(
                no=2,
                agent="product_research",
                description="评分",
                answer=None,
                citations=(),
                executed=True,
                incomplete="正文为空:作答轮未产出正文(重试后仍为空),任务未完成",
            ),
        ),
        plan=slices_from_plan(PLAN),
        corpus_fingerprint="f" * 64,
        corpus_batch_id=None,
        dataset_run_id="ds-run-1",
        recorded_at=RECORDED_AT,
    )


def _snapshot_with_hits() -> Snapshot:
    """命中池非空的一份快照(#75 B):切片 1 命中两块(其中 ``#913`` 没被引用),切片 2 无检索轨迹。"""
    base = _snapshot()
    return replace(
        base,
        slices=(
            replace(base.slices[0], hits=("usitc-digital-trade#1", "usitc-digital-trade#913")),
            base.slices[1],
        ),
    )


def test_write_read_roundtrip(tmp_path: Path) -> None:
    """写-读往返:字段一一还原(含嵌套 citations、空批次留 None、切片序稳定)。"""
    path = write_snapshot(tmp_path / "runs" / "run-1", _snapshot())  # 目录不存在即建
    assert path == snapshot_path(tmp_path / "runs" / "run-1", "coffee-maker-us")

    restored = read_snapshot(path)
    assert restored == _snapshot()


def test_write_is_idempotent_by_scenario(tmp_path: Path) -> None:
    """同一场景重跑即覆盖同文件(快照文件名 = 场景 id,run 目录决定成组)。"""
    run_dir = tmp_path / "run-1"
    write_snapshot(run_dir, _snapshot())
    write_snapshot(run_dir, _snapshot())
    assert len(list(run_dir.glob("*.json"))) == 1


def test_payload_is_readable_json(tmp_path: Path) -> None:
    """落盘为可读 JSON(中文不转义、版本号在载荷里)——快照要能人眼复核。"""
    path = write_snapshot(tmp_path, _snapshot())
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == SNAPSHOT_VERSION
    assert raw["corpus_batch_id"] is None
    assert "选品报告" in path.read_text(encoding="utf-8")


def test_snapshot_distinguishes_not_executed_blank_and_incomplete() -> None:
    """#64 A3:三态可辨——「没执行 / 空产出 / 未完成」不得在快照里同形(B30④)。

    依据:任务线的 ``executed`` 恒 false(ReAct 子图返回的 dict 根本没这个键,``graph.py`` 的
    ``if run.get("executed")`` 永不成立),于是「切片没跑」与「跑了但空产出」长得一模一样——
    2026-09-16 读快照时被它误导过。修法:``executed`` 如实 + ``incomplete``(未完成原因)落盘。
    """
    snapshot = Snapshot(
        scenario_id="s",
        surface="选品报告",
        run_name="r",
        thread_id="t",
        trace_id="x",
        status="completed",
        slices=(
            SliceOutput(no=1, agent="a", description="d", answer="结论", citations=(), executed=True),
            SliceOutput(
                no=2, agent="a", description="d", answer=None, citations=(), executed=True, incomplete="步数超限"
            ),
            SliceOutput(no=3, agent="a", description="d", answer=None, citations=(), executed=False, incomplete=None),
        ),
        plan=(),
        corpus_fingerprint="f" * 64,
        corpus_batch_id=None,
        dataset_run_id=None,
        recorded_at=RECORDED_AT,
    )

    states = {(item.executed, item.answer is None, item.incomplete is not None) for item in snapshot.slices}
    assert len(states) == 3, "三态必须在快照字段上互不相同"


def test_incomplete_reason_roundtrips(tmp_path: Path) -> None:
    """未完成原因随快照落盘并可读回(排查时不只知道「没答案」,还知道为什么)。"""
    restored = read_snapshot(write_snapshot(tmp_path, _snapshot()))

    assert restored.slices[1].incomplete is not None
    assert "正文为空" in restored.slices[1].incomplete
    assert restored.slices[0].incomplete is None  # 正常产出不凭空多一个未完成标记


def test_evidence_roundtrips_and_bad_shape_is_explicit(tmp_path: Path) -> None:
    """查证证据块随快照落盘并可读回(#67);形状坏掉即报错点名,不当空处理。

    任务线没有这份载荷 ⇒ None(如实缺席:判分材料据此不渲染该段——与「有证据块但三类皆空」
    是两回事,后者是「查过,没查到」)。
    """
    restored = read_snapshot(write_snapshot(tmp_path, _snapshot()))

    assert restored.slices[0].evidence == EVIDENCE
    assert restored.slices[1].evidence is None

    payload = json.loads(snapshot_path(tmp_path, "coffee-maker-us").read_text(encoding="utf-8"))
    payload["slices"][0]["evidence"] = "不是映射"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="evidence 须是映射"):
        read_snapshot(bad)


def test_slices_from_results_reads_incomplete_marker() -> None:
    """API 载荷里的 incomplete 原样读入(results 是它的唯一来源)。"""
    slices = slices_from_results({"1": {"agent": "a", "description": "d", "incomplete": "步数超限(10)"}})

    assert slices[0].answer is None
    assert slices[0].incomplete == "步数超限(10)"


def test_slices_from_results_parses_api_payload() -> None:
    """GET /api/tasks 的 results(切片号字符串键)→ 按切片号升序的切片列表。"""
    slices = slices_from_results(
        {
            "2": {"agent": "product_research", "description": "评分", "answer": "B 级", "executed": True},
            "1": {
                "agent": "product_research",
                "description": "检索",
                "answer": "结论 [1]",
                "executed": True,
                "citations": CITATIONS,
            },
        }
    )
    assert [item.no for item in slices] == [1, 2]
    assert slices[0].citations == tuple(CITATIONS)
    assert slices[1].answer == "B 级"
    assert slices[1].citations == ()  # 无引用载荷 → 空(不编)


def test_hits_roundtrip_and_bad_shape_is_explicit(tmp_path: Path) -> None:
    """检索命中池随快照落盘并可读回(#75 B,只落 id);形状坏掉即报错点名,不当空处理。

    没有检索轨迹的线(工作台线 / 没跑检索的切片)如实为空元组——「没命中」与「没有这份清单」
    在快照上都读成空,与 citations 的留空同一姿势(如实,不编)。
    """
    restored = read_snapshot(write_snapshot(tmp_path, _snapshot_with_hits()))

    assert restored.slices[0].hits == ("usitc-digital-trade#1", "usitc-digital-trade#913")
    assert restored.slices[1].hits == ()

    payload = json.loads(snapshot_path(tmp_path, "coffee-maker-us").read_text(encoding="utf-8"))
    payload["slices"][0]["hits"] = [1, 2]
    bad = tmp_path / "bad-hits.json"
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="hits 须是切块标识字符串清单"):
        read_snapshot(bad)


def test_slices_from_results_reads_retrieval_hit_ids() -> None:
    """API 载荷里的 hits(本片命中池)→ 去重保序的标识清单;缺席如实为空(旧形状载荷不报错)。"""
    slices = slices_from_results(
        {
            "1": {"agent": "a", "description": "d", "hits": ["d#1", "d#2"]},
            "2": {"agent": "a", "description": "d"},
        }
    )

    assert slices[0].hits == ("d#1", "d#2")
    assert slices[1].hits == ()


def test_slices_from_results_rejects_broken_shape() -> None:
    """形状不符即报错(API 契约违反而非「没产出」);空/缺席如实返回空列表。"""
    assert slices_from_results(None) == ()
    assert slices_from_results({}) == ()
    with pytest.raises(ValueError, match="映射"):
        slices_from_results(["not-a-mapping"])
    with pytest.raises(ValueError, match="切片号 'x'"):
        slices_from_results({"x": {"agent": "a"}})
    with pytest.raises(ValueError, match="不是映射"):
        slices_from_results({"1": "not-a-mapping"})


def test_slices_from_plan_parses_api_payload() -> None:
    """GET /api/tasks 的 plan 段 → 按切片号升序的规划切片(依赖声明与审批点原样保留)。"""
    plan = slices_from_plan(PLAN)

    assert [item.no for item in plan] == [1, 2]  # 载荷给的是 2、1,读回按切片号升序
    assert plan[0].agent == "product_research"
    assert plan[0].depends_on == ()
    assert plan[0].approval_points == ("上架",)
    assert plan[1].depends_on == (1,)


def test_slices_from_plan_rejects_broken_shape() -> None:
    """形状不符即报错(API 契约违反);缺席(工作台线无规划段)如实返回空。"""
    assert slices_from_plan(None) == ()
    assert slices_from_plan({"slices": []}) == ()
    with pytest.raises(ValueError, match="须是清单"):
        slices_from_plan({"slices": "not-a-list"})
    with pytest.raises(ValueError, match="切片号"):
        slices_from_plan({"slices": [{"no": 1}, {"no": "x"}]})
    with pytest.raises(ValueError, match="depends_on"):
        slices_from_plan({"slices": [{"no": 1, "depends_on": ["1"]}]})


def test_plan_lines_render_dependencies_and_approval_points() -> None:
    """规划段 → 判据提示词的可读行:片数、业务域、说明、依赖、审批点(缺即标「无」)。"""
    lines = plan_lines(slices_from_plan(PLAN))

    assert lines[0] == "(共 2 片)"
    assert lines[1] == "切片 1:业务域 product_research | 说明:检索市场情报 | 依赖:无 | 审批点:上架"
    assert lines[2] == "切片 2:业务域 product_research | 说明:评分 | 依赖:1 | 审批点:无"


def test_read_rejects_bad_shape(tmp_path: Path) -> None:
    """坏快照报错点名文件与字段(不静默按新形状解释旧数据)。"""
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="不是合法 JSON"):
        read_snapshot(path)

    path.write_text(json.dumps({"version": SNAPSHOT_VERSION - 1, "scenario_id": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="不受支持"):
        read_snapshot(path)

    path.write_text(json.dumps({"version": SNAPSHOT_VERSION, "scenario_id": "x"}), encoding="utf-8")
    with pytest.raises(ValueError, match="缺字段"):
        read_snapshot(path)

    payload = json.loads((write_snapshot(tmp_path, _snapshot())).read_text(encoding="utf-8"))
    payload["recorded_at"] = "不是时刻"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="ISO 8601"):
        read_snapshot(path)


def test_read_rejects_broken_plan_shape(tmp_path: Path) -> None:
    """快照里的规划段坏形状 → 报错点名文件(不把坏数据当「没有计划」静默吞掉)。"""
    payload = json.loads((write_snapshot(tmp_path, _snapshot())).read_text(encoding="utf-8"))
    payload["plan"] = [{"no": "x"}]
    path = tmp_path / "bad-plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=r"bad-plan\.json"):
        read_snapshot(path)


def test_snapshot_without_plan_reads_as_empty(tmp_path: Path) -> None:
    """同一版本内 ``plan`` 缺席 → 空规划段(版本闸管**旧版断代**,不管字段级留空,两回事)。"""
    payload = json.loads((write_snapshot(tmp_path, _snapshot())).read_text(encoding="utf-8"))
    del payload["plan"]
    path = tmp_path / "no-plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert read_snapshot(path).plan == ()
