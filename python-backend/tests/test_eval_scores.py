"""缝 2(票 #59):Langfuse 落分面——「落了什么」经假 client 断言;读回核实避免「写没写进去都不知道」。

langfuse 4.x 的 ``create_score`` 把异常吞成日志(不抛),故写完 flush 后必须读回核对:
本文件的读回用例即把这条保险钉住(不触真网)。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from python_backend.evals.scores import LangfuseScores
from python_backend.evals.scoring import ScoreRecord


class FakeScoresEndpoint:
    """假 scores 端点:按 trace_id 返回预置分数(真 SDK 的 ``client.api.scores``)。"""

    def __init__(self, pages: list[list[SimpleNamespace]] | None = None) -> None:
        self.pages = pages if pages is not None else []
        self.calls: list[dict] = []

    def get_many(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        page = self.pages.pop(0) if self.pages else []
        return SimpleNamespace(data=page)


class FakeLangfuseClient:
    """假 Langfuse client:记录 ``create_score`` 调用,并提供读回端点。"""

    def __init__(self, pages: list[list[SimpleNamespace]] | None = None) -> None:
        self.scores: list[dict] = []
        self.api = SimpleNamespace(scores=FakeScoresEndpoint(pages))
        self.flushed = 0

    def create_score(self, **kwargs: object) -> None:
        self.scores.append(kwargs)

    def flush(self) -> None:
        self.flushed += 1


def _record(
    name: str = "coffee-maker-us#1#1",
    *,
    dataset_run_id: str | None = "ds-run-1",
    trace_id: str = "a3f1c2d4e5b60718293a4b5c6d7e8f90",
    judge_model: str = "",
) -> ScoreRecord:
    return ScoreRecord(
        name=name,
        value=1,
        comment="有据可依",
        trace_id=trace_id,
        dataset_run_id=dataset_run_id,
        metadata={"corpus_fingerprint": "f" * 64},
        judge_model=judge_model,
    )


def _server_side(record: ScoreRecord) -> SimpleNamespace:
    return SimpleNamespace(name=record.name, value=float(record.value), trace_id=record.trace_id)


def test_write_anchors_on_trace_and_carries_dataset_run_in_metadata() -> None:
    """锚 = trace_id(服务端三选一,实测同发 datasetRunId 被 400 拒);dataset run 走 metadata。"""
    client = FakeLangfuseClient()
    record = _record()

    LangfuseScores(client).write(record)

    assert client.scores == [
        {
            "score_id": record.score_id,
            "name": "coffee-maker-us#1#1",
            "value": 1,
            "trace_id": "a3f1c2d4e5b60718293a4b5c6d7e8f90",
            "comment": "有据可依",
            "metadata": {"corpus_fingerprint": "f" * 64, "dataset_run_id": "ds-run-1"},
        }
    ]


def test_write_carries_judge_model_in_metadata_when_present() -> None:
    """所判型号随分落库(judge 线);机械线无型号,不塞空字段(如实,不编)。"""
    client = FakeLangfuseClient()

    LangfuseScores(client).write(_record(judge_model="claude-opus-5"))
    LangfuseScores(client).write(_record("coffee-maker-us#1#机械", judge_model=""))

    assert client.scores[0]["metadata"]["judge_model"] == "claude-opus-5"
    assert "judge_model" not in client.scores[1]["metadata"]


def test_score_id_is_deterministic_per_stable_key() -> None:
    """确定性 score_id:同一稳定键恒同一 id(换 rubric 重评覆盖同一条,而不是叠一层)。"""
    assert _record().score_id == _record().score_id  # 同键 ⇒ 同 id(理由/值变也不换 id)
    assert _record().score_id != _record("coffee-maker-us#1#机械").score_id
    assert _record().score_id.startswith("eval-")


def test_write_omits_dataset_run_metadata_when_absent() -> None:
    """投影缺席(无 dataset run)→ metadata 不塞空字段(如实,不编)。"""
    client = FakeLangfuseClient()

    LangfuseScores(client).write(_record(dataset_run_id=None))

    assert client.scores[0]["metadata"] == {"corpus_fingerprint": "f" * 64}


def test_flush_delegates_to_client() -> None:
    """收尾冲刷经注入 client(写完不冲,最近写入可能还没上报)。"""
    client = FakeLangfuseClient()
    LangfuseScores(client).flush()
    assert client.flushed == 1


def test_verify_reads_back_per_trace() -> None:
    """读回逐条按 trace_id 查(锚所在);读回齐了即放行。"""
    records = [_record("coffee-maker-us#1#1"), _record("coffee-maker-us#1#机械")]
    client = FakeLangfuseClient([[_server_side(record) for record in records]])

    LangfuseScores(client).verify(records, sleep=lambda _seconds: None, attempts=1)

    assert client.api.scores.calls == [{"trace_id": records[0].trace_id, "limit": 100}]


def test_verify_reads_back_every_distinct_trace() -> None:
    """多条 trace 的分数要逐条 trace 查——单按 dataset run 读回会让只挂 trace 的分读不回来。"""
    first = _record("a#1#1", trace_id="1" * 32)
    second = _record("b#1#1", trace_id="2" * 32)
    client = FakeLangfuseClient([[_server_side(first)], [_server_side(second)]])

    LangfuseScores(client).verify([first, second], sleep=lambda _seconds: None, attempts=1)

    assert client.api.scores.calls == [
        {"trace_id": "1" * 32, "limit": 100},
        {"trace_id": "2" * 32, "limit": 100},
    ]


def test_verify_retries_until_ingestion_catches_up() -> None:
    """分数经 ingestion 异步落库:第一次读回不齐先等再读,追上即放行(不拿一次读空就报错)。"""
    record = _record()
    client = FakeLangfuseClient([[], [_server_side(record)]])
    slept: list[float] = []

    LangfuseScores(client).verify([record], sleep=slept.append, attempts=3)

    assert len(client.api.scores.calls) == 2
    assert len(slept) == 1


def test_verify_reports_missing_scores() -> None:
    """读回始终不齐 → 报错点名缺哪几条(读回键与缺失清单都在),不假装成功。"""
    record = _record()
    client = FakeLangfuseClient([[]])

    with pytest.raises(RuntimeError) as error:
        LangfuseScores(client).verify([record], sleep=lambda _seconds: None, attempts=2)

    assert "分数读回不齐" in str(error.value)
    assert "coffee-maker-us#1#1" in str(error.value)
    assert "trace_id" in str(error.value)


def test_verify_rejects_same_name_on_other_trace() -> None:
    """同名分数挂错 trace 也算缺:核对键带 trace_id(换 rubric 重评后同名分可能在别的 trace 上)。"""
    record = _record(trace_id="1" * 32)
    impostor = SimpleNamespace(name=record.name, value=1.0, trace_id="2" * 32)
    client = FakeLangfuseClient([[impostor]])

    with pytest.raises(RuntimeError, match="分数读回不齐"):
        LangfuseScores(client).verify([record], sleep=lambda _seconds: None, attempts=1)
