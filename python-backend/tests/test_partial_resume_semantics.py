"""D2 前置实验:部分 resume 语义(spec #6 D2「验证后定」)。

断言 langgraph 1.2.11 支持「只恢复 pending 子集」:两并行分支都 interrupt,
只 resume 其中一个 → 该分支完成、另一分支保持挂起,再次 resume 后全部完成。
通过 → D2 走即时逐批;失败 → D2 退回批量(决定齐后一次全量 resume_map)。

本测试同时是行为钉:增量 3 resume 代码依赖此语义,留作回归防线。
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt


class ExperimentState(TypedDict):
    answers: Annotated[list[str], operator.add]


def _node_a(state: ExperimentState) -> dict:
    answer = interrupt({"batch": "a"})
    return {"answers": [f"a:{answer}"]}


def _node_b(state: ExperimentState) -> dict:
    answer = interrupt({"batch": "b"})
    return {"answers": [f"b:{answer}"]}


def _build() -> CompiledStateGraph:
    # langgraph 泛型/stub 边界:StateGraph(TypedDict) 静态检查必报(运行时合法,官方文档模式)
    builder = StateGraph(ExperimentState)  # ty: ignore
    builder.add_node("node_a", _node_a)
    builder.add_node("node_b", _node_b)
    builder.add_edge(START, "node_a")
    builder.add_edge(START, "node_b")
    builder.add_edge("node_a", END)
    builder.add_edge("node_b", END)
    return builder.compile(checkpointer=InMemorySaver())


async def _drain(stream) -> None:
    """排干事件流以驱动图跑至完成/挂起。"""
    async for _event in stream:
        pass


async def test_partial_resume_only_resumes_target_branch() -> None:
    """核心断言:部分 resume_map 只恢复对应分支,其余保持挂起。"""
    graph = _build()
    config = {"configurable": {"thread_id": "experiment-1"}}

    # 第一步:两分支都挂起,得到两个 interrupt id
    first = await graph.astream_events({"answers": []}, config, version="v3")  # ty: ignore
    await _drain(first)
    interrupts = list(await first.interrupts())
    assert len(interrupts) == 2, f"预期两分支都挂起,实际 {len(interrupts)}"
    by_value = {i.value["batch"]: i.id for i in interrupts}

    # 第二步:只 resume A → A 完成、B 保持挂起
    resumed = await graph.astream_events(Command(resume={by_value["a"]: "ok-a"}), config, version="v3")  # ty: ignore
    await _drain(resumed)
    state = await graph.aget_state(config)  # ty: ignore
    assert state.values["answers"] == ["a:ok-a"], f"部分 resume 后应只有 A 的答案,实际 {state.values['answers']}"

    pending = list(await resumed.interrupts())
    assert len(pending) == 1, f"B 应保持挂起,实际 pending={pending}"
    assert pending[0].id == by_value["b"]

    # 第三步:再 resume B → 全部完成
    final = await graph.astream_events(Command(resume={by_value["b"]: "ok-b"}), config, version="v3")  # ty: ignore
    await _drain(final)
    state = await graph.aget_state(config)  # ty: ignore
    assert sorted(state.values["answers"]) == ["a:ok-a", "b:ok-b"]
