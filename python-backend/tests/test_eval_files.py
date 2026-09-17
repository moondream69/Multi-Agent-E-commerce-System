"""入仓评测真源契约(票 #56):``docs/evals/*.yaml`` 可被加载器校验,选品场景至少一条。

离线纯函数(不触网、不触服务)——它是跑批的上游,坏了先在这里红。
"""

from __future__ import annotations

from pathlib import Path

from python_backend.evals.schema import Scenario, load_scenarios

EVALS_DIR = Path(__file__).resolve().parents[2] / "docs" / "evals"

# 选品场景的语料覆盖锚:锚 = 情报语料里的**段标题原文**(下方用例逐个 grep 校验)。
# 新增/重铸选品场景时同步登记并写进 note——清单与场景集双向核对,漏登记即红。
CORPUS_ANCHORS = {
    "smart-home-us": "Smart Homes",
    "smart-band-us": "Wearables",
    "remote-health-us": "Remote Healthcare Monitoring",
}


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


def test_product_scenarios_anchor_to_corpus_coverage() -> None:
    """选品场景的品类须在情报语料里有**实检索面**(#64 B4 / #66 两次同因错配的机械守卫)。

    依据:``coffee-maker-us``(便携咖啡机)与 ``outdoor-trend``(户外运动)两次错配——品类在情报
    语料零覆盖时,切片只能如实拒答,而判据要的分级结论/趋势判断本就不可能产出,场景于是测的是
    **语料边界**而不是 Agent 能力(用户故事 13)。旧守卫只钉 ``smart-home-us`` 一条的 note 文案,
    错配仍能从别的场景溜进来(``outdoor-trend`` 的 note 压根没写覆盖依据);故此守卫改为**逐条**
    核对,且核对的是**语料文件本身**(锚文本 grep 得到)而不只是场景自述。
    判据条数不变仍是两条——重铸不改判据口径(评分等级 + 有检索依据)。
    """
    corpus = (EVALS_DIR.parent / "corpus" / "market-intel.yaml").read_text(encoding="utf-8")
    reports = {scenario.id: scenario for scenario in _scenarios() if scenario.surface == "选品报告"}

    assert "coffee-maker-us" not in reports, "零覆盖品类已重铸,不得回潮"
    assert "outdoor-trend" not in reports, "零覆盖品类已重铸,不得回潮"
    assert set(reports) == set(CORPUS_ANCHORS), "选品场景与覆盖锚清单须一一对应(新增/重铸场景要登记锚)"

    for scenario_id, anchor in CORPUS_ANCHORS.items():
        scenario = reports[scenario_id]
        assert anchor in scenario.note, f"{scenario_id}:note 未点名覆盖锚 {anchor!r}"
        assert anchor in corpus, f"{scenario_id}:覆盖锚 {anchor!r} 在情报语料里找不到——品类零覆盖"
        assert len(scenario.rubric) == 2, f"{scenario_id}:判据条数须为 2(评分等级 + 有检索依据)"
