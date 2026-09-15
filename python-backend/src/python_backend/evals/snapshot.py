"""产出快照(spec #55 B / 票 #58):跑批的**本地真源**——``score`` 只读快照回评,不重跑任务。

一条场景一次运行 = 一个 JSON 文件(``<快照目录>/<run 名>/<场景 id>.json``):场景 id / thread_id /
trace_id / **切片级产出**(answer / citations / executed)/ 语料版本锚 / 时间。目录由调用方给定
(CLI 默认 ``docs/evals/runs/``,gitignore)。

**切片级保真,不拼顶层长文**:citations 编号是**切片内**编号(``build_citations`` 每片各自从 1 排),
把多片答案拼成长文会让机械防伪引(#57)的编号集跨片串味——A 片幻觉的 ``[2]`` 撞上 B 片合法的 ``[2]``
就洗成了合法引用(假通过)。故快照保持 ``slices[]`` 分组,回评逐片判。

语料版本锚两种(ADR-0008):``corpus_fingerprint`` 为主锚(真源内容哈希,离线可重算、不受净库重建
影响)、``corpus_batch_id`` 为尽力附记(台账读不到即 null,如实标注);``dataset_run_id`` 由投影回填,
供 #59 的分数与 dataset run 互链。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# 快照 schema 版本:字段增删即升版(读旧快照时报错清晰,不静默按新形状解释)
SNAPSHOT_VERSION = 1


@dataclass(frozen=True)
class SliceOutput:
    """一个切片的产品面产出(``results`` 里的一条,字段与 graph.py 的 run_output 对齐)。"""

    no: int
    agent: str
    description: str
    answer: str | None
    citations: tuple[dict, ...]
    executed: bool


@dataclass(frozen=True)
class Snapshot:
    """一条场景的一次运行(快照文件名 = ``<scenario_id>.json``)。"""

    scenario_id: str
    surface: str
    run_name: str
    thread_id: str
    trace_id: str
    status: str
    slices: tuple[SliceOutput, ...]
    corpus_fingerprint: str
    corpus_batch_id: str | None  # 摄入台账附记:读不到即 None(如实标注,不编)
    dataset_run_id: str | None  # Langfuse dataset run 回填(#59 落分互链);投影未成功即 None
    recorded_at: datetime


def slices_from_results(results: object) -> tuple[SliceOutput, ...]:
    """``GET /api/tasks/{thread_id}`` 的 ``results``(切片号 → 产出)→ 切片列表(按切片号升序)。

    形状不符(条目不是映射 / 切片号不是整数)即报错——那是 API 契约违反而非「没产出」;
    空/缺席(线程未跑、checkpointer 无状态)如实返回空列表,由调用方判「无可评产出」。
    """
    if results is None:
        return ()
    if not isinstance(results, dict):
        raise ValueError(f"results 须是「切片号 → 产出」映射,实际是 {type(results).__name__}")
    slices: list[SliceOutput] = []
    for key, entry in results.items():
        if not isinstance(entry, dict):
            raise ValueError(f"切片 {key} 的产出不是映射(实际 {type(entry).__name__})")
        try:
            number = int(key)
        except (TypeError, ValueError) as error:
            raise ValueError(f"切片号 {key!r} 不是整数") from error
        slices.append(
            SliceOutput(
                no=number,
                agent=str(entry.get("agent", "")),
                description=str(entry.get("description", "")),
                answer=entry.get("answer"),
                citations=tuple(entry.get("citations") or ()),
                executed=bool(entry.get("executed")),
            )
        )
    return tuple(sorted(slices, key=lambda item: item.no))


def snapshot_path(run_dir: Path, scenario_id: str) -> Path:
    """快照文件路径 = ``<run 目录>/<场景 id>.json``(场景 id 全局唯一,即文件名的唯一性来源)。"""
    return run_dir / f"{scenario_id}.json"


def write_snapshot(run_dir: Path, snapshot: Snapshot) -> Path:
    """落盘快照(目录不存在即建);返回写入路径。同路径重复写即覆盖(重跑同场景即更新产出)。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(run_dir, snapshot.scenario_id)
    path.write_text(json.dumps(_payload(snapshot), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_snapshot(path: Path) -> Snapshot:
    """读快照 → 快照对象;形状非法即报错(报错须指明**哪个文件、哪个字段**)。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}:不是合法 JSON({error})") from error
    if not isinstance(raw, dict):
        raise ValueError(f"{path}:顶层须是映射,不是 {type(raw).__name__}")
    version = raw.get("version")
    if version != SNAPSHOT_VERSION:
        raise ValueError(f"{path}:快照版本 {version!r} 不受支持(本实现只读 version={SNAPSHOT_VERSION})")
    required = ("scenario_id", "surface", "run_name", "thread_id", "trace_id", "status", "recorded_at")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"{path}:缺字段 {'、'.join(missing)}")
    entries = raw.get("slices")
    if not isinstance(entries, list):
        raise ValueError(f"{path}:slices 须是清单")
    try:
        recorded_at = datetime.fromisoformat(str(raw["recorded_at"]))
    except ValueError as error:
        raise ValueError(f"{path}:recorded_at 不是 ISO 8601 时刻({raw['recorded_at']!r})") from error
    return Snapshot(
        scenario_id=str(raw["scenario_id"]),
        surface=str(raw["surface"]),
        run_name=str(raw["run_name"]),
        thread_id=str(raw["thread_id"]),
        trace_id=str(raw["trace_id"]),
        status=str(raw["status"]),
        slices=tuple(_slice_from_json(entry, path) for entry in entries),
        corpus_fingerprint=str(raw.get("corpus_fingerprint", "")),
        corpus_batch_id=_optional_str(raw.get("corpus_batch_id")),
        dataset_run_id=_optional_str(raw.get("dataset_run_id")),
        recorded_at=recorded_at,
    )


def _slice_from_json(entry: Any, path: Path) -> SliceOutput:
    if not isinstance(entry, dict):
        raise ValueError(f"{path}:slices 的条目须是映射,不是 {type(entry).__name__}")
    citations = entry.get("citations") or []
    if not isinstance(citations, list):
        raise ValueError(f"{path}:切片 {entry.get('no')} 的 citations 须是清单")
    return SliceOutput(
        no=int(entry["no"]),
        agent=str(entry.get("agent", "")),
        description=str(entry.get("description", "")),
        answer=entry.get("answer"),
        citations=tuple(citations),
        executed=bool(entry.get("executed")),
    )


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _payload(snapshot: Snapshot) -> dict:
    """快照对象 → JSON 载荷(字段名即 schema;改这里等于改 schema,与 SNAPSHOT_VERSION 同步)。"""
    return {
        "version": SNAPSHOT_VERSION,
        "scenario_id": snapshot.scenario_id,
        "surface": snapshot.surface,
        "run_name": snapshot.run_name,
        "thread_id": snapshot.thread_id,
        "trace_id": snapshot.trace_id,
        "status": snapshot.status,
        "slices": [
            {
                "no": item.no,
                "agent": item.agent,
                "description": item.description,
                "answer": item.answer,
                "citations": list(item.citations),
                "executed": item.executed,
            }
            for item in snapshot.slices
        ],
        "corpus_fingerprint": snapshot.corpus_fingerprint,
        "corpus_batch_id": snapshot.corpus_batch_id,
        "dataset_run_id": snapshot.dataset_run_id,
        "recorded_at": snapshot.recorded_at.isoformat(),
    }
