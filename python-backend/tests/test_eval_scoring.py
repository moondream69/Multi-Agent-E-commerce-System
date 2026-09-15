"""缝 2(票 #59):``score`` 编排分支——注入假 judge 与假分数池,断言「发了什么、落了什么」。

离线:不触网、不触 Langfuse、不烧 token。快照经**真读写**(``snapshot.py`` 的落盘格式)产生,
故也是快照回读路径的联测;机械防伪引判直接消费 ``core/citations.py`` 的纯函数(#57)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from python_backend.evals.judge import JudgeError, JudgeRequest, RubricScore
from python_backend.evals.schema import Scenario
from python_backend.evals.scoring import (
    ScoreRecord,
    load_run_snapshots,
    score_run,
)
from python_backend.evals.snapshot import SliceOutput, Snapshot, write_snapshot
from python_backend.infrastructure.tracing import eval_root_trace_id

NOW = datetime(2026, 9, 15, 6, 0, tzinfo=UTC)
FINGERPRINT = "f" * 64
CITATIONS = (
    {
        "number": 1,
        "doc_id": "usitc-digital-trade",
        "title": "全球数字贸易",
        "source": "USITC",
        "chunks": [{"id": "usitc-digital-trade#3"}],
    },
)
ANSWER_WITH_CITATIONS = "美国市场咖啡机需求上行 [1]"


def _scenario(scenario_id: str = "coffee-maker-us", rubric: tuple[str, ...] = ("判据甲", "判据乙")) -> Scenario:
    return Scenario(id=scenario_id, surface="选品报告", input="分析一下便携咖啡机在美国市场的选品机会", rubric=rubric)


def _workbench_scenario() -> Scenario:
    """工作台线场景(票 #60):无任务线程的快照,产出 = 一条草稿。"""
    return Scenario(
        id="cs-workbench-shipping-en",
        surface="客服草稿·工作台",
        input="How long does delivery usually take?",
        rubric=("不编造发货时效", "结论有查证证据支撑"),
        locale="en",
    )


def _snapshot(
    *slices: SliceOutput,
    scenario_id: str = "coffee-maker-us",
    surface: str = "选品报告",
    thread_id: str = "t-1",
    trace_id: str = "a3f1c2d4e5b60718293a4b5c6d7e8f90",
    dataset_run_id: str | None = "ds-run-1",
) -> Snapshot:
    return Snapshot(
        scenario_id=scenario_id,
        surface=surface,
        run_name="run-1",
        thread_id=thread_id,
        trace_id=trace_id,
        status="completed",
        slices=slices,
        corpus_fingerprint=FINGERPRINT,
        corpus_batch_id="batch-1",
        dataset_run_id=dataset_run_id,
        recorded_at=NOW,
    )


def _slice(
    answer: str = ANSWER_WITH_CITATIONS,
    citations: tuple[dict, ...] = CITATIONS,
    no: int = 1,
    agent: str = "product_research",
) -> SliceOutput:
    return SliceOutput(
        no=no,
        agent=agent,
        description="检索情报并给出结论",
        answer=answer,
        citations=citations,
        executed=True,
    )


class FakeJudge:
    """假 judge:记录「发了什么」,按请求里的判据逐条给出固定判定(默认全过)。"""

    def __init__(self, scores: list[RubricScore] | None = None, *, fail_on: str | None = None) -> None:
        self.requests: list[JudgeRequest] = []
        self._scores = scores
        self._fail_on = fail_on

    def judge(self, request: JudgeRequest) -> list[RubricScore]:
        self.requests.append(request)
        if self._fail_on is not None and request.scenario_id == self._fail_on:
            raise JudgeError(f"judge 调用失败(场景 {request.scenario_id}):500")
        if self._scores is not None:
            return self._scores
        return [
            RubricScore(index=index, passed=True, comment=f"判据{index}成立")
            for index in range(1, len(request.criteria) + 1)
        ]


class RecordingSink:
    """假分数池:记录「落了什么」(先例 = tests/conftest.py 的 Recording* 形状)。"""

    def __init__(self) -> None:
        self.records: list[ScoreRecord] = []

    def write(self, record: ScoreRecord) -> None:
        self.records.append(record)


def _write_multi_slice_run(run_dir: Path) -> None:
    """一份快照含两片产出(切片号 1/2)——一条场景一次运行一条产物,多片写在同一条里。"""
    write_snapshot(run_dir, _snapshot(_slice(no=1), _slice(no=2, answer="第二片 [1]")))


def test_mechanical_and_judge_scores_land_with_stable_keys(tmp_path: Path) -> None:
    """一条场景两片产出 → 机械线 + judge 线各出分;键 = ``场景id#切片号#判据序号``(机械线为 #机械)。"""
    _write_multi_slice_run(tmp_path / "run-1")
    judge = FakeJudge()
    sink = RecordingSink()

    result = score_run(
        load_run_snapshots(tmp_path / "run-1"),
        [_scenario()],
        judge=judge,
        sink=sink,
    )

    assert [record.name for record in sink.records] == [
        "coffee-maker-us#1#机械",
        "coffee-maker-us#1#1",
        "coffee-maker-us#2#机械",
        "coffee-maker-us#2#2",
    ]
    assert all(record.value == 1 for record in sink.records)  # 引用对得上 + 假 judge 全过
    assert result.records == tuple(sink.records)
    assert result.passed == 4


def test_judge_receives_slice_level_context(tmp_path: Path) -> None:
    """切片级保真:一次请求 = 一片(不拼多片长文),带该片答案、该片引用载荷、场景输入与判据。"""
    _write_multi_slice_run(tmp_path / "run-1")
    judge = FakeJudge()

    score_run(load_run_snapshots(tmp_path / "run-1"), [_scenario()], judge=judge, sink=RecordingSink())

    assert [request.slice_no for request in judge.requests] == [1, 2]
    assert [request.criteria for request in judge.requests] == [("判据甲",), ("判据乙",)]  # 条数相等 → 一一对应
    first = judge.requests[0]
    assert first.scenario_id == "coffee-maker-us"
    assert first.input == "分析一下便携咖啡机在美国市场的选品机会"
    assert first.answer == ANSWER_WITH_CITATIONS
    assert first.citations == CITATIONS
    assert judge.requests[1].answer == "第二片 [1]"


def test_full_score_records_link_trace_dataset_run_and_corpus_anchor(tmp_path: Path) -> None:
    """分数互链:挂任务 trace + dataset run id;metadata 带语料指纹(主锚)与批次号(附记)。"""
    write_snapshot(tmp_path / "run-1", _snapshot(_slice()))
    sink = RecordingSink()

    score_run(
        load_run_snapshots(tmp_path / "run-1"),
        [_scenario()],
        judge=FakeJudge(),
        sink=sink,
        judge_model="claude-opus-5",
    )

    record = sink.records[0]
    assert record.trace_id == "a3f1c2d4e5b60718293a4b5c6d7e8f90"
    assert record.dataset_run_id == "ds-run-1"
    assert record.metadata == {
        "scenario_id": "coffee-maker-us",
        "surface": "选品报告",
        "slice_no": 1,
        "thread_id": "t-1",
        "run_name": "run-1",
        "corpus_fingerprint": FINGERPRINT,
        "corpus_batch_id": "batch-1",
        "criterion": "机械防伪引:答案引用标记 ⇄ citations 载荷一一对应,残留未解析标记 = 疑似伪造",
    }


def test_judge_model_rides_judge_scores_only(tmp_path: Path) -> None:
    """所判型号随 judge 分落库(换 judge 即换分数,不记型号无从按版本归因);机械线无 judge,不记。"""
    write_snapshot(tmp_path / "run-1", _snapshot(_slice()))
    sink = RecordingSink()

    score_run(
        load_run_snapshots(tmp_path / "run-1"),
        [_scenario()],
        judge=FakeJudge(),
        sink=sink,
        judge_model="claude-opus-5",
    )

    by_name = {record.name: record for record in sink.records}
    assert by_name["coffee-maker-us#1#1"].judge_model == "claude-opus-5"
    assert by_name["coffee-maker-us#1#1"].metadata["criterion"] == "判据甲"
    assert by_name["coffee-maker-us#1#机械"].judge_model == ""  # 机械判据零 LLM,无型号可记


def test_judge_comment_and_criterion_land_in_score_record(tmp_path: Path) -> None:
    """judge 的 0/1 与理由原样落分(comment);判据文案随 metadata(稳定键只带序号)。"""
    write_snapshot(tmp_path / "run-1", _snapshot(_slice()))
    judge = FakeJudge(scores=[RubricScore(index=1, passed=False, comment="只给了品类介绍,没有评分等级")])
    sink = RecordingSink()

    score_run(load_run_snapshots(tmp_path / "run-1"), [_scenario(rubric=("给出评分等级",))], judge=judge, sink=sink)

    judge_record = next(record for record in sink.records if record.name.endswith("#1"))
    assert judge_record.value == 0
    assert judge_record.comment == "只给了品类介绍,没有评分等级"
    assert judge_record.metadata["criterion"] == "给出评分等级"


def test_criteria_pairing_broadcasts_when_counts_differ(tmp_path: Path) -> None:
    """判据条数 != 切片数 → 每条判据对每片(广播);序号仍取判据在真源 rubric 里的位置。"""
    _write_multi_slice_run(tmp_path / "run-1")
    judge = FakeJudge()

    sink = RecordingSink()
    score_run(
        load_run_snapshots(tmp_path / "run-1"),
        [_scenario(rubric=("判据甲", "判据乙", "判据丙"))],
        judge=judge,
        sink=sink,
    )

    assert [request.criteria for request in judge.requests] == [("判据甲", "判据乙", "判据丙")] * 2
    assert [record.name for record in sink.records if "#机械" not in record.name] == [
        "coffee-maker-us#1#1",
        "coffee-maker-us#1#2",
        "coffee-maker-us#1#3",
        "coffee-maker-us#2#1",
        "coffee-maker-us#2#2",
        "coffee-maker-us#2#3",
    ]


def test_mechanical_check_flags_residual_marker_as_fake_citation(tmp_path: Path) -> None:
    """伪造场景:答案残留解析不到的标记(载荷里没有这个编号)→ 机械线判 0,理由点名标记。"""
    write_snapshot(tmp_path / "run-1", _snapshot(_slice(answer="需求上行 [1],另有论断 [7]")))
    sink = RecordingSink()

    score_run(load_run_snapshots(tmp_path / "run-1"), [_scenario()], judge=FakeJudge(), sink=sink)

    mechanical = next(record for record in sink.records if record.name.endswith("#机械"))
    assert mechanical.value == 0
    assert "残留标记" in mechanical.comment and "[7]" in mechanical.comment


def test_mechanical_check_skips_when_nothing_to_check(tmp_path: Path) -> None:
    """既无标记也无载荷(查单/评分类产出)→ 不落机械分(不适用,不作通过计);judge 线照判。"""
    write_snapshot(tmp_path / "run-1", _snapshot(_slice(answer="没有引用,纯说明", citations=())))
    sink = RecordingSink()

    score_run(load_run_snapshots(tmp_path / "run-1"), [_scenario()], judge=FakeJudge(), sink=sink)

    assert [record.name for record in sink.records] == ["coffee-maker-us#1#1", "coffee-maker-us#1#2"]


def test_workbench_snapshot_falls_back_to_eval_root_trace(tmp_path: Path) -> None:
    """工作台线快照不带任务 trace → 挂跑批器自建的评测根 trace(按场景 id 确定性派生)。"""
    write_snapshot(tmp_path / "run-1", _snapshot(_slice(), trace_id=""))
    sink = RecordingSink()

    score_run(load_run_snapshots(tmp_path / "run-1"), [_scenario()], judge=FakeJudge(), sink=sink)

    assert {record.trace_id for record in sink.records} == {eval_root_trace_id("coffee-maker-us")}


def test_workbench_snapshot_scores_like_task_line(tmp_path: Path) -> None:
    """工作台线快照与任务线**同一份消费**(#60 的零分叉):机械线 + judge 线照跑,两条线的分数形状一致。

    该线快照随带的就是自建根 trace(跑批器建的那条),``thread_id`` 空如实进 metadata。
    """
    trace_id = eval_root_trace_id("cs-workbench-shipping-en")
    write_snapshot(
        tmp_path / "run-1",
        _snapshot(
            _slice(agent="drafting"),
            scenario_id="cs-workbench-shipping-en",
            surface="客服草稿·工作台",
            thread_id="",
            trace_id=trace_id,
        ),
    )
    judge = FakeJudge()
    sink = RecordingSink()

    result = score_run(load_run_snapshots(tmp_path / "run-1"), [_workbench_scenario()], judge=judge, sink=sink)

    assert [record.name for record in sink.records] == [
        "cs-workbench-shipping-en#1#机械",
        "cs-workbench-shipping-en#1#1",
        "cs-workbench-shipping-en#1#2",
    ]
    assert all(record.value == 1 for record in sink.records)  # 引用对得上 + 假 judge 全过
    assert result.passed == 3
    assert {record.trace_id for record in sink.records} == {trace_id}
    assert {record.dataset_run_id for record in sink.records} == {"ds-run-1"}
    assert all(record.metadata["surface"] == "客服草稿·工作台" for record in sink.records)
    assert all(record.metadata["thread_id"] == "" for record in sink.records)
    assert judge.requests[0].input == "How long does delivery usually take?"


def test_snapshot_without_dataset_run_id_scores_without_link(tmp_path: Path) -> None:
    """投影缺席(dataset_run_id 为 None)时照评:分数照落,只是不挂 dataset run(如实,不编)。"""
    write_snapshot(tmp_path / "run-1", _snapshot(_slice(), dataset_run_id=None))
    sink = RecordingSink()

    score_run(load_run_snapshots(tmp_path / "run-1"), [_scenario()], judge=FakeJudge(), sink=sink)

    assert all(record.dataset_run_id is None for record in sink.records)


def test_unknown_scenario_id_in_snapshot_fails_explicitly(tmp_path: Path) -> None:
    """快照的场景不在真源里 → 报错点名(评的是真源,不是残影),不静默漏评。"""
    write_snapshot(tmp_path / "run-1", _snapshot(_slice(), scenario_id="ghost-scenario"))

    with pytest.raises(RuntimeError, match="ghost-scenario"):
        score_run(load_run_snapshots(tmp_path / "run-1"), [_scenario()], judge=FakeJudge(), sink=RecordingSink())


def test_judge_failure_aborts_with_no_judge_scores_landed(tmp_path: Path) -> None:
    """judge 失败即中止:该片**一条分都不落**(整片判定先组装、后写池,judge 抛错即无写入)。"""
    write_snapshot(tmp_path / "run-1", _snapshot(_slice()))
    judge = FakeJudge(fail_on="coffee-maker-us")
    sink = RecordingSink()

    with pytest.raises(JudgeError, match="judge 调用失败"):
        score_run(load_run_snapshots(tmp_path / "run-1"), [_scenario()], judge=judge, sink=sink)

    assert [record.name for record in sink.records] == []  # 记录只在判定通过时落池:失败即零写入


def test_missing_run_dir_and_empty_dir_fail_explicitly(tmp_path: Path) -> None:
    """回评要有可评对象:目录不存在 / 目录里没快照,报错都指向「先跑 run」。"""
    with pytest.raises(RuntimeError, match="先跑"):
        load_run_snapshots(tmp_path / "nowhere")

    (tmp_path / "empty").mkdir()
    with pytest.raises(RuntimeError, match="没有快照"):
        load_run_snapshots(tmp_path / "empty")
