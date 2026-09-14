"""judge 配置闸(票 #56):三键经环境读取;缺任一显式报错。离线(不触网、不触中转站)。

「不静默降级」与 ``EmbeddingService`` 同哲学:缺配置时宁可炸,也不产出一份看着成功的空评测。
"""

from __future__ import annotations

import pytest

from python_backend.evals.judge import JudgeConfig, load_judge_config
from python_backend.settings import Settings

JUDGE_ENV = {"JUDGE_MODEL": "claude-opus-5", "JUDGE_API_URL": "https://relay.example.com", "JUDGE_API_KEY": "sk-relay"}


def test_judge_group_is_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """三键经环境映射读取(.env 与 compose 共读的同一机制)。"""
    for name, value in JUDGE_ENV.items():
        monkeypatch.setenv(name, value)

    config = load_judge_config(Settings())

    assert config == JudgeConfig(model="claude-opus-5", api_url="https://relay.example.com", api_key="sk-relay")


def test_load_judge_config_returns_endpoint_triple() -> None:
    """配置齐全 → 端点三键(判分客户端据此建连)。显式入参优先于 .env,测试不读真实 judge 配置。"""
    settings = Settings(
        judge_model="claude-opus-5", judge_api_url="https://relay.example.com", judge_api_key="sk-relay"
    )

    assert load_judge_config(settings) == JudgeConfig(
        model="claude-opus-5", api_url="https://relay.example.com", api_key="sk-relay"
    )


@pytest.mark.parametrize("missing", sorted(JUDGE_ENV))
def test_load_judge_config_rejects_missing_field(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    """缺任一键即炸,且报错点名缺的是哪个(用环境变量名,便于照着 .env 改)——留空与不写等价。"""
    for name, value in JUDGE_ENV.items():
        monkeypatch.setenv(name, "" if name == missing else value)

    with pytest.raises(RuntimeError, match=missing):
        load_judge_config(Settings())
