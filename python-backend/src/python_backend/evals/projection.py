"""Langfuse 投影(spec #55 B):场景集 → dataset;每条 run → dataset run item + 任务 trace 关联。

真源在仓(``docs/evals/*.yaml``),datasets 只是它的投影——同「知识库」哲学(ADR-0008):
可重灌、同 id 幂等 upsert(item id = 场景 id;分数按键落,判据稳定键的载体)。

缺密钥**显式报错**(与 ``judge.py`` / ``EmbeddingService``「不静默降级」同哲学):一份没落库的
「评测」比跑失败更坏。host 缺省取本机约定(自托管栈的 3001),不填也能跑;密钥一个都不能少。

client 可注入(先例 = ``infrastructure/tracing.py`` 的 ``LangfuseTaskTracer``:注入记录式假件、
不触真网)。写入面三件全经注入 client:langfuse 4.x 的 dataset run **没有高层方法**,只有
``client.api.dataset_run_items.create(run_name=…)``(run 由 run_name 隐式聚合,不预建)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from python_backend.evals.schema import Scenario
from python_backend.settings import Settings, get_settings

# 评测 dataset 名:全部场景一张表(surface 入 item metadata)——item id 全局唯一(场景 id 即全局唯一),
# 跨 dataset 复用 id 会被 Langfuse 拒,按面拆表只会换来同一个约束的多个爆炸点
DATASET_NAME = "agent-evals"
# 本机约定:自托管 Langfuse 栈映射 3001(app 容器内是 http://langfuse-server:3000,那是 compose 的活)
DEFAULT_LOCAL_HOST = "http://localhost:3001"


@dataclass(frozen=True)
class LangfuseConfig:
    """Langfuse 落库三键(host 有本机缺省;双密钥缺一不可)。"""

    host: str
    public_key: str
    secret_key: str


def load_langfuse_config(settings: Settings | None = None) -> LangfuseConfig:
    """读 Langfuse 配置;缺双密钥即显式报错(不静默降级为「跑完但没落库」)。

    host 与 app 侧的语义**有意不同**:app(``tracing.py``)是「host 留空 = 观测禁用」,评测 CLI 是
    本机进程、compose 的 ``LANGFUSE_HOST`` 不适用,故留空取本机约定 3001——单键两义已在
    ``settings.py`` 与 ``.env.example`` 与调用方打印里三处收口(票 #58 评审修)。
    """
    settings = settings if settings is not None else get_settings()
    missing = [
        name
        for name, value in (
            ("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key),
            ("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key),
        )
        if not value.strip()
    ]
    if missing:
        raise RuntimeError(
            f"Langfuse 未配置:{'、'.join(missing)} 为空——评测产出与分数都要落 Langfuse"
            "(启用步骤见 OPERATIONS 生产切换清单第 6 步)"
        )
    return LangfuseConfig(
        host=settings.langfuse_host.strip() or DEFAULT_LOCAL_HOST,
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
    )


class Projection(Protocol):
    """跑批依赖的投影面(测试注入记录式假件;实现 = ``LangfuseProjection``)。

    只列 run 用到的两件;``flush`` 属 SDK 收尾(CLI 在真投影上调用),不进本协议。
    """

    def sync_scenarios(self, scenarios: list[Scenario]) -> None: ...

    def record_run(self, *, run_name: str, scenario_id: str, trace_id: str, metadata: dict) -> str | None: ...


class LangfuseProjection:
    """Langfuse 写入面:场景集 upsert 进 dataset + 每次运行落 dataset run item(trace 互链)。"""

    def __init__(self, client: Any, *, dataset_name: str = DATASET_NAME) -> None:
        self._client = client
        self._dataset_name = dataset_name

    def sync_scenarios(self, scenarios: list[Scenario]) -> None:
        """场景集 → dataset + 逐条 item(input = 场景输入,rubric 入 metadata);同 id 幂等 upsert。"""
        self._client.create_dataset(
            name=self._dataset_name,
            description="评测金标场景(真源 = docs/evals/*.yaml;由 evals.py run 投影,可重灌)",
        )
        for scenario in scenarios:
            self._client.create_dataset_item(
                dataset_name=self._dataset_name,
                id=scenario.id,
                input=scenario.input,
                metadata={"surface": scenario.surface, "rubric": list(scenario.rubric), "note": scenario.note},
            )

    def record_run(self, *, run_name: str, scenario_id: str, trace_id: str, metadata: dict) -> str | None:
        """一条场景的一次运行 → dataset run item(挂任务 trace);返回 dataset_run_id(供分数互链)。

        任务 trace 的 id 由 thread_id 确定性派生(``task_trace_id``),app 容器已在同一 Langfuse
        实例上建过它——本调用只是把「这条 run 的产出」接到那条 trace 上。
        """
        item = self._client.api.dataset_run_items.create(
            run_name=run_name,
            dataset_item_id=scenario_id,
            trace_id=trace_id,
            metadata=metadata,
        )
        return getattr(item, "dataset_run_id", None)

    def flush(self) -> None:
        """冲掉 SDK 缓冲(CLI 退出前调用,否则最近写入可能还没上报)。"""
        self._client.flush()


def build_projection(settings: Settings | None = None) -> LangfuseProjection:
    """真实投影:过配置闸(缺密钥显式报错)→ 官方 SDK client。"""
    config = load_langfuse_config(settings)
    from langfuse import Langfuse

    return LangfuseProjection(Langfuse(public_key=config.public_key, secret_key=config.secret_key, host=config.host))
