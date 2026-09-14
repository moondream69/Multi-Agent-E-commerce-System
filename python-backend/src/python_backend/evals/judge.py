"""judge 面(spec #55 C/D):评测判据的配置闸;判分客户端见票 #59。

judge 是**独立于被评对象**的跨厂模型(中转站 Claude),配置三键 ``judge_*`` 与业务 LLM 的
``llm_*`` 分离——换 judge 是配置面操作,不动产品面。
"""

from __future__ import annotations

from dataclasses import dataclass

from python_backend.settings import Settings, get_settings


@dataclass(frozen=True)
class JudgeConfig:
    """judge 端点三键(票 #59 的判分客户端据此建连)。"""

    model: str
    api_url: str
    api_key: str


def load_judge_config(settings: Settings | None = None) -> JudgeConfig:
    """读 judge 三键;缺任一即显式报错(与 ``EmbeddingService``「不静默降级」同哲学)。

    缺配置时静默跑出的「评测」没有判分——比跑失败更坏的,是一份看着成功的空结果。
    """
    settings = settings if settings is not None else get_settings()
    missing = [
        name
        for name, value in (
            ("JUDGE_MODEL", settings.judge_model),
            ("JUDGE_API_URL", settings.judge_api_url),
            ("JUDGE_API_KEY", settings.judge_api_key),
        )
        if not value.strip()
    ]
    if missing:
        raise RuntimeError(f"judge 未配置:{'、'.join(missing)} 为空——判据需要中转站 judge(见 .env.example)")
    return JudgeConfig(model=settings.judge_model, api_url=settings.judge_api_url, api_key=settings.judge_api_key)
