"""枚举列声明面守卫(issue #22,离线):遍历 ``Base.metadata`` 自动发现全部 ``sa.Enum`` 列。

取代 #12 遗留的硬编码五表清单 —— 此前新增第六个枚举列不会被任何测试发现(守卫名单是死的)。
断言细则与口径写在 ``tests.conftest.assert_enum_columns_declared_safely``;零 PG 依赖,随快速套件离线跑。

行为面(多行批插不 cast / 裸 SQL 断言小写 value / ORM 回读成员)仍需真 PG,见 ``test_enum_columns.py``。
"""

from __future__ import annotations

from tests.conftest import assert_enum_columns_declared_safely


def test_enum_columns_auto_discovered_and_declared_safely() -> None:
    """发现的枚举列逐一过声明面断言;发现集须非空(遍历失效时守卫会空转,须显式拦下)。"""
    discovered = assert_enum_columns_declared_safely()
    assert discovered, "Base.metadata 未发现任何枚举列 —— 遍历失效或模型未注册,守卫空转"
