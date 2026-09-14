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
