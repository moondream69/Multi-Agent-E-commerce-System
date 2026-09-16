"""入仓评测真源契约(票 #56):``docs/evals/*.yaml`` 可被加载器校验,选品场景至少一条。

离线纯函数(不触网、不触服务)——它是跑批的上游,坏了先在这里红。
"""

from __future__ import annotations

from pathlib import Path

from python_backend.evals.schema import Scenario, load_scenarios

EVALS_DIR = Path(__file__).resolve().parents[2] / "docs" / "evals"


def _scenarios() -> list[Scenario]:
    return load_scenarios(sorted(EVALS_DIR.glob("*.yaml")))


def test_shipped_scenarios_load() -> None:
    """真源在仓且形状合法(id 唯一 / surface 合法 / rubric 非空 / 输入非空)。"""
    assert _scenarios(), f"{EVALS_DIR} 下没有评测真源"


def test_tracer_surface_is_product_report() -> None:
    """tracer 顺序:选品报告先行——真源须先立住这一格(客服草稿 / 规划切片后补)。"""
    reports = [scenario for scenario in _scenarios() if scenario.surface == "选品报告"]

    assert reports, "选品报告是首个评测面,真源至少一条"
    for scenario in reports:
        assert scenario.rubric, f"{scenario.id}:判据为空——没有判据的场景判不出分"
        assert scenario.note, f"{scenario.id}:缺备注(场景要写明它守住什么)"


def test_reworked_product_scenario_declares_corpus_coverage() -> None:
    """#64 B4:重铸的选品场景须写明**语料覆盖依据**,且判据条数不变。

    依据:``coffee-maker-us`` 的品类在情报语料零覆盖——切片只能如实拒答,而判据要的分级结论本就
    不可能产出,场景于是测的是**语料边界**而不是 Agent 能力(用户故事 13)。重铸后的场景必须把
    「这个品类在语料里有可检索面」写在 note 里,否则同一类错配还能再溜进来。
    """
    reports = {scenario.id: scenario for scenario in _scenarios() if scenario.surface == "选品报告"}

    assert "coffee-maker-us" not in reports, "零覆盖品类已重铸,不得回潮"
    assert "smart-home-us" in reports, "重铸后的选品场景缺席"

    reworked = reports["smart-home-us"]
    assert "语料" in reworked.note and "覆盖" in reworked.note, "note 未写明语料覆盖依据"
    assert len(reworked.rubric) == 2, "重铸不改判据口径(仍是评分等级 + 有检索依据两条)"
