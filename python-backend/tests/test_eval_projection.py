"""缝 2(票 #58):Langfuse 投影面——配置闸 + 「发了什么」(记录式假 client,不触真网)。

先例 = ``tests/test_tracing.py`` 的 FakeLangfuseClient(假件形状照真 SDK 的调用面)。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from python_backend.evals.projection import DATASET_NAME, LangfuseProjection, load_langfuse_config
from python_backend.evals.schema import Scenario
from python_backend.settings import Settings


class FakeDatasetRunItems:
    """假 dataset_run_items 端点(真 SDK 的 ``client.api.dataset_run_items``)。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(dataset_run_id="ds-run-1")


class FakeApi:
    def __init__(self) -> None:
        self.dataset_run_items = FakeDatasetRunItems()


class FakeSpan:
    """假 observation 上下文(真 SDK 的 ``start_as_current_observation`` 返回**上下文管理器**)。"""

    def __init__(self, spans: list[dict], kwargs: dict) -> None:
        self._spans = spans
        self._kwargs = kwargs

    def __enter__(self) -> FakeSpan:
        self._spans.append(self._kwargs)  # 真 SDK 在进入时建 observation(与 trace)
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeLangfuseClient:
    """假 Langfuse client:记录 dataset / item / run item / span 四处写入,不依赖真 SDK。"""

    def __init__(self) -> None:
        self.datasets: list[dict] = []
        self.items: list[dict] = []
        self.spans: list[dict] = []
        self.api = FakeApi()
        self.flushed = 0

    def create_dataset(self, **kwargs: object) -> None:
        self.datasets.append(kwargs)

    def create_dataset_item(self, **kwargs: object) -> None:
        self.items.append(kwargs)

    def start_as_current_observation(self, **kwargs: object) -> FakeSpan:
        return FakeSpan(self.spans, kwargs)

    def flush(self) -> None:
        self.flushed += 1


def _scenario() -> Scenario:
    return Scenario(
        id="coffee-maker-us",
        surface="选品报告",
        input="分析一下便携咖啡机在美国市场的选品机会",
        rubric=("给出明确的评分等级", "结论性论断有检索依据"),
        note="首条金标场景",
    )


def test_config_gate_names_missing_keys() -> None:
    """缺任一双密钥即显式报错并点名(不静默降级为「跑完但没落库」)。"""
    with pytest.raises(RuntimeError, match="LANGFUSE_PUBLIC_KEY"):
        load_langfuse_config(Settings(langfuse_public_key="", langfuse_secret_key="sk"))
    with pytest.raises(RuntimeError, match="LANGFUSE_SECRET_KEY"):
        load_langfuse_config(Settings(langfuse_public_key="pk", langfuse_secret_key=""))
    with pytest.raises(RuntimeError, match="未配置"):
        load_langfuse_config(Settings(langfuse_public_key="  ", langfuse_secret_key="  "))


def test_config_gate_host_defaults_to_local_convention() -> None:
    """host 有本机缺省(自托管栈 3001);配置了就照配置走。"""
    configured = load_langfuse_config(
        Settings(langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_host="http://langfuse:3000")
    )
    assert configured.host == "http://langfuse:3000"
    defaulted = load_langfuse_config(Settings(langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_host="  "))
    assert defaulted.host == "http://localhost:3001"


def test_sync_scenarios_upserts_items_keyed_by_scenario_id() -> None:
    """场景集 → dataset + 逐条 item:id = 场景 id(同 id 幂等 upsert),rubric 入 metadata。"""
    client = FakeLangfuseClient()
    LangfuseProjection(client).sync_scenarios([_scenario()])

    assert [dataset["name"] for dataset in client.datasets] == [DATASET_NAME]
    assert len(client.items) == 1
    item = client.items[0]
    assert item["dataset_name"] == DATASET_NAME
    assert item["id"] == "coffee-maker-us"
    assert item["input"] == "分析一下便携咖啡机在美国市场的选品机会"
    assert item["metadata"] == {
        "surface": "选品报告",
        "rubric": ["给出明确的评分等级", "结论性论断有检索依据"],
        "note": "首条金标场景",
    }


def test_record_run_links_task_trace_and_returns_dataset_run_id() -> None:
    """每次运行一条 dataset run item:run 名成组、dataset item 挂场景、trace 挂任务 trace。"""
    client = FakeLangfuseClient()
    run_id = LangfuseProjection(client).record_run(
        run_name="run-20260915T040000Z",
        scenario_id="coffee-maker-us",
        trace_id="a3f1c2d4e5b60718293a4b5c6d7e8f90",
        metadata={"surface": "选品报告", "corpus_fingerprint": "f" * 64},
    )

    assert run_id == "ds-run-1"
    assert client.api.dataset_run_items.calls == [
        {
            "run_name": "run-20260915T040000Z",
            "dataset_item_id": "coffee-maker-us",
            "trace_id": "a3f1c2d4e5b60718293a4b5c6d7e8f90",
            "metadata": {"surface": "选品报告", "corpus_fingerprint": "f" * 64},
        }
    ]


def test_create_eval_trace_builds_root_trace_with_readable_name() -> None:
    """工作台线评测根 trace:带 trace_context 的 span——langfuse 4.x 随 observation 隐式建 trace,
    trace 名即该 observation 名(可读标记 ``eval:<场景id>``,探针实测见票 #60 证据)。"""
    client = FakeLangfuseClient()
    LangfuseProjection(client).create_eval_trace(
        trace_id="a3f1c2d4e5b60718293a4b5c6d7e8f90",
        name="eval:cs-workbench-stock-zh",
        input="桌面收纳架 深空黑款现在还有货吗?",
        output="目前在库 2 件 [1]",
    )

    assert client.spans == [
        {
            "name": "eval:cs-workbench-stock-zh",
            "as_type": "span",
            "trace_context": {"trace_id": "a3f1c2d4e5b60718293a4b5c6d7e8f90"},
            "input": "桌面收纳架 深空黑款现在还有货吗?",
            "output": "目前在库 2 件 [1]",
        }
    ]


def test_flush_delegates_to_client() -> None:
    """收尾冲刷经注入 client(CLI 退出前调用,否则最近写入可能没上报)。"""
    client = FakeLangfuseClient()
    LangfuseProjection(client).flush()
    assert client.flushed == 1
