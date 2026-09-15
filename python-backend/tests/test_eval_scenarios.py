"""评测场景加载器(票 #56 缝):给定 YAML 输入 → 场景对象 / 报错行为。离线纯逻辑(不触网、不触服务)。

坏真源不许静默入批——报错须指明**哪条、哪个字段**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from python_backend.evals.schema import load_scenarios

VALID = """
version: 1
scenarios:
  - id: coffee-maker-us
    surface: 选品报告
    input: |-
      分析一下便携咖啡机在美国市场的选品机会
    rubric:
      - 给出评分等级并说明依据
      - 结论有检索依据
    note: 首条
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_load_scenarios_reads_fixed_input_and_rubric(tmp_path: Path) -> None:
    """真源读回:固定输入(多行原样)+ 判据清单 + 备注。"""
    path = _write(tmp_path, "product-report.yaml", VALID)

    [scenario] = load_scenarios([path])

    assert scenario.id == "coffee-maker-us"
    assert scenario.surface == "选品报告"
    assert scenario.input == "分析一下便携咖啡机在美国市场的选品机会"
    assert scenario.rubric == ("给出评分等级并说明依据", "结论有检索依据")
    assert scenario.note == "首条"


def test_load_scenarios_merges_files_in_order(tmp_path: Path) -> None:
    """多文件按传入顺序合并(id 是跨文件的键,顺序决定跑批顺序);备注可省。"""
    first = _write(tmp_path, "a.yaml", VALID)
    second = _write(
        tmp_path,
        "b.yaml",
        """
scenarios:
  - id: restock-check
    surface: 规划切片
    input: 看看哪些商品库存需要补货
    rubric: [依赖声明与先序一致]
""",
    )

    scenarios = load_scenarios([first, second])

    assert [scenario.id for scenario in scenarios] == ["coffee-maker-us", "restock-check"]
    assert scenarios[1].note == ""


def test_load_scenarios_rejects_duplicate_id_across_files(tmp_path: Path) -> None:
    """全局唯一:同一 id 出现在两个文件里也是冲突(快照与 dataset item 以 id 为键)。"""
    first = _write(tmp_path, "a.yaml", VALID)
    second = _write(tmp_path, "b.yaml", VALID)

    with pytest.raises(ValueError) as error:
        load_scenarios([first, second])

    assert "coffee-maker-us" in str(error.value)  # 哪条
    assert "a.yaml" in str(error.value)  # 以及首次出现在哪


def test_load_scenarios_rejects_missing_id(tmp_path: Path) -> None:
    """缺 id:报错须能定位到「第几条」(此时无 id 可依)。"""
    path = _write(
        tmp_path,
        "a.yaml",
        """
scenarios:
  - surface: 选品报告
    input: 分析一下便携咖啡机
    rubric: [有检索依据]
""",
    )

    with pytest.raises(ValueError, match="缺 id"):
        load_scenarios([path])


def test_load_scenarios_rejects_unknown_surface(tmp_path: Path) -> None:
    """surface 是评测面的枚举(决定打哪个入口、按哪套维度判),非法即报错并列出可选值。"""
    path = _write(
        tmp_path,
        "a.yaml",
        """
scenarios:
  - id: coffee-maker-us
    surface: 选品分析
    input: 分析一下便携咖啡机
    rubric: [有检索依据]
""",
    )

    with pytest.raises(ValueError) as error:
        load_scenarios([path])

    assert "coffee-maker-us" in str(error.value)  # 哪条
    assert "surface" in str(error.value)  # 哪个字段
    assert "选品报告" in str(error.value)  # 可选值


def test_load_scenarios_rejects_empty_rubric(tmp_path: Path) -> None:
    """判据清单非空:没有判据的场景判不出分,等于没评。"""
    path = _write(
        tmp_path,
        "a.yaml",
        """
scenarios:
  - id: coffee-maker-us
    surface: 选品报告
    input: 分析一下便携咖啡机
    rubric: []
""",
    )

    with pytest.raises(ValueError) as error:
        load_scenarios([path])

    assert "coffee-maker-us" in str(error.value)
    assert "rubric" in str(error.value)


def test_load_scenarios_rejects_duplicate_criterion(tmp_path: Path) -> None:
    """判据文案唯一:判据文案是稳定键的索引依据,重复即「第几条」有歧义——收分时对不上号。"""
    path = _write(
        tmp_path,
        "a.yaml",
        """
scenarios:
  - id: coffee-maker-us
    surface: 选品报告
    input: 分析一下便携咖啡机
    rubric: [结论有检索依据, 结论有检索依据]
""",
    )

    with pytest.raises(ValueError, match="重复判据"):
        load_scenarios([path])


def test_load_scenarios_rejects_empty_input(tmp_path: Path) -> None:
    """输入非空:输入是固定文本入仓(LLM 不生成),空输入的场景无从跑起。"""
    path = _write(
        tmp_path,
        "a.yaml",
        """
scenarios:
  - id: coffee-maker-us
    surface: 选品报告
    input: '   '
    rubric: [有检索依据]
""",
    )

    with pytest.raises(ValueError) as error:
        load_scenarios([path])

    assert "coffee-maker-us" in str(error.value)
    assert "input" in str(error.value)


def test_load_scenarios_defaults_locale_to_zh(tmp_path: Path) -> None:
    """locale 可省:缺省 zh(工作台线的目标语言;其余线本就不看它)。"""
    path = _write(tmp_path, "product-report.yaml", VALID)

    [scenario] = load_scenarios([path])

    assert scenario.locale == "zh"


def test_load_scenarios_reads_locale_for_workbench_line(tmp_path: Path) -> None:
    """工作台线带 locale:目标语言经端点参数下发(B19 多语草稿的评测入口)。"""
    path = _write(
        tmp_path,
        "customer-draft.yaml",
        """
scenarios:
  - id: cs-workbench-shipping-en
    surface: 客服草稿·工作台
    input: How long does delivery usually take?
    locale: en
    rubric: [结论有查证证据支撑]
""",
    )

    [scenario] = load_scenarios([path])

    assert scenario.locale == "en"


def test_load_scenarios_rejects_locale_on_other_surfaces(tmp_path: Path) -> None:
    """locale 只对工作台线有意义:其余线上带它即报错——静默忽略等于让作者以为换了语言(草稿纹丝不动)。"""
    path = _write(
        tmp_path,
        "a.yaml",
        """
scenarios:
  - id: coffee-maker-us
    surface: 选品报告
    input: 分析一下便携咖啡机
    locale: en
    rubric: [有检索依据]
""",
    )

    with pytest.raises(ValueError) as error:
        load_scenarios([path])

    assert "coffee-maker-us" in str(error.value)  # 哪条
    assert "locale" in str(error.value)  # 哪个字段
    assert "客服草稿·工作台" in str(error.value)  # 以及它只对哪条线有效


def test_load_scenarios_rejects_empty_locale(tmp_path: Path) -> None:
    """locale 留空即无从起草(工作台线的语言不从消息文本推),报错而非兜底猜一个。"""
    path = _write(
        tmp_path,
        "a.yaml",
        """
scenarios:
  - id: cs-workbench-shipping-en
    surface: 客服草稿·工作台
    input: How long does delivery take?
    locale: '  '
    rubric: [有检索依据]
""",
    )

    with pytest.raises(ValueError, match="locale 为空"):
        load_scenarios([path])


def test_load_scenarios_rejects_non_list_scenarios(tmp_path: Path) -> None:
    """顶层形状坏掉时也要有清晰报错(不是 AttributeError 崩栈)。"""
    path = _write(tmp_path, "a.yaml", "scenarios:\n  coffee-maker-us: 选品报告\n")

    with pytest.raises(ValueError, match="scenarios"):
        load_scenarios([path])


def test_load_scenarios_rejects_non_mapping_document(tmp_path: Path) -> None:
    """整份文件不成形状(顶层是清单)时同样报错清晰——坏真源不许崩栈。"""
    path = _write(tmp_path, "a.yaml", "- id: coffee-maker-us\n")

    with pytest.raises(ValueError) as error:
        load_scenarios([path])

    assert "a.yaml" in str(error.value)
    assert "顶层" in str(error.value)
