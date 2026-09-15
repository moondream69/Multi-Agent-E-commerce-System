"""评测场景文件 schema(spec #55 A「金标场景集」):冻结入仓的 YAML 是评测真源。

Langfuse datasets 只是它的投影——同「知识库」哲学:真源在仓、可 diff、可冻结、可重灌。
**LLM 不生成评测输入**:输入全部是固定文本,可复盘、不随生成漂移。

文件形状(version 1)::

    version: 1
    scenarios:
      - id: <全局唯一标识>              # 跨文件也唯一(快照与 dataset item 的键)
        surface: <选品报告 | 客服草稿·工作台 | 客服草稿·任务内 | 规划切片>
        input: |-                        # 固定输入文本(选品=指令;客服=买家消息)
          ...
        rubric:                          # LLM-as-judge 判据(每条 0/1 + 理由)
          - <判据一句话>
        note: <备注:这条场景想守住什么>

判据的稳定键 = ``<场景id>#<切片号>#<判据序号>``(与语料切块标识 ``<文档标识>#<序号>`` 同法):分数按键
落,改判据文案不改键——否则「换 rubric 只重跑 score」(#55 AC4)之后,历史分数会断成两条线。
**切片号段**是回评面(#59)补的必要段:一条场景产出**多片**切片,无此段则两片的同号判据撞同一个键。
**判据文案须不重复**(加载器拒重复):序号是键的索引依据,两条同文案的判据会让键歧义、收分对不上号。

机械防伪引(答案引用标记 ⇄ citations 载荷一一对应)是**结构性判据**:对带引用的产出恒跑、
不设开关,且不随场景变化——故不入 rubric 列(列进去只是把同一句话抄进每条场景)。判定
**按产出形状生效**:产出带标记或 citations 载荷时出 0/1,两者皆无时判**不适用**而非「通过」
——查了零个对象的通过分会稀释跨场景指标,也会掩盖「本该有引用却一个都没落」(那是 LLM
判据「结论有检索依据」的活,机械判据认不出:引用载荷是命中才带,缺了即无可判)。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# 评测面:决定场景怎么跑(打哪个入口)与判据维度(spec #55 Solution C)
SURFACES = ("选品报告", "客服草稿·工作台", "客服草稿·任务内", "规划切片")


@dataclass(frozen=True)
class Scenario:
    """一条金标场景:固定输入 + 判据清单(真源文件里的一条)。"""

    id: str
    surface: str
    input: str
    rubric: tuple[str, ...]
    note: str = ""


def load_scenarios(paths: list[Path]) -> list[Scenario]:
    """读评测场景文件(真源)→ 场景列表;坏数据即报错(不许静默入批)。

    id 在**全部文件间**唯一:跑批以 id 为快照与 dataset item 的键,重复即冲突。
    """
    scenarios: list[Scenario] = []
    origins: dict[str, Path] = {}
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path}:顶层须是映射(version + scenarios),不是 {type(raw).__name__}")
        entries = raw.get("scenarios", [])
        if not isinstance(entries, list):
            raise ValueError(f"{path}:scenarios 须是清单")
        for index, entry in enumerate(entries, start=1):
            scenario = _build_scenario(entry, path, index)
            if scenario.id in origins:
                raise ValueError(f"{path}:场景 id 重复({scenario.id};已见于 {origins[scenario.id]})——id 须全局唯一")
            origins[scenario.id] = path
            scenarios.append(scenario)
    return scenarios


def _build_scenario(entry: Any, path: Path, index: int) -> Scenario:
    """一条场景 → 场景对象;字段非法即报错(报错须指明**哪条、哪个字段**)。"""
    if not isinstance(entry, dict):
        raise ValueError(f"{path}:第 {index} 条场景不是映射(须是 id / surface / input / rubric 的形状)")
    scenario_id = str(entry.get("id", "")).strip()
    if not scenario_id:
        raise ValueError(f"{path}:第 {index} 条场景缺 id(须非空、全局唯一)")
    where = f"{path}:{scenario_id}"
    surface = entry.get("surface")
    if not isinstance(surface, str) or surface not in SURFACES:
        raise ValueError(f"{where} 的 surface 非法({surface!r};可选 {SURFACES})")
    text = str(entry.get("input", "")).strip()
    if not text:
        raise ValueError(f"{where} 缺 input(输入须非空固定文本,LLM 不生成评测输入)")
    rubric = entry.get("rubric")
    if not isinstance(rubric, list) or not rubric:
        raise ValueError(f"{where} 的 rubric 为空或非清单(至少一条判据)")
    criteria = tuple(str(item).strip() for item in rubric)
    if any(not criterion for criterion in criteria):
        raise ValueError(f"{where} 的 rubric 含空判据")
    if len(set(criteria)) != len(criteria):
        raise ValueError(f"{where} 的 rubric 含重复判据(判据文案是稳定键的索引依据,重复即歧义)")
    return Scenario(
        id=scenario_id,
        surface=surface,
        input=text,
        rubric=criteria,
        note=str(entry.get("note", "")).strip(),
    )
