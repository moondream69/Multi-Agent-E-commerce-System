"""Langfuse 分数写入面(spec #55 B/C / 票 #59):分数挂任务 trace,带 dataset run 与语料锚。

与 ``projection.py`` 同一条注入面:client 可注入(测试用记录式假件,不触真网);缺密钥的闸在
``load_langfuse_config``(CLI 在建连前就过闸),此处不重复检查。

**锚只给一个**(实测,2026-09-15):Langfuse ingestion 对 score-create 事件收
「``traceId`` / ``sessionId`` / ``datasetRunId`` 三选一」——同发 ``traceId`` + ``datasetRunId``
被 400 拒(高层 SDK 的 ``create_score`` 签名允许两者同传,是签名的宽,不是服务的允)。
取舍:锚取 ``trace_id``(分数进任务 trace 的 Scores 面板,「从分数跳到 trace 看全过程」是主用途);
``dataset_run_id`` 经 **metadata** 随带(按 dataset run 归组、可查)+ 落进 ``score_id``(确定性 id,
换 rubric 重评覆盖同一条,不叠层)。★ 待维护者确认:若 dataset run 面板的关联优先于 trace 面板,
把锚换成 ``dataset_run_id`` 即可(一行),代价是分数不再挂 trace。

**落分要看得见**:langfuse 4.x 的 ``create_score`` 内部把异常吞成日志(不抛)——一份「写没写
进去都不知道」的评分类似于静默降级。故 ``verify`` 在 ``flush`` 后从服务端读回,逐条核对分数名与
值;对不上即报错(带读到的分数名清单),不假装成功。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from python_backend.evals.scoring import ScoreRecord

# 读回等待:分数经 ingestion 异步落库,写入后立刻查可能还没进(实测有秒级延迟)
VERIFY_ATTEMPTS = 10
VERIFY_INTERVAL_SECONDS = 3.0
# 单条 trace 的读回上限(一条 trace 的分数 = 切片数 x 判据数,几十条足够;超出即截断报缺)
_READBACK_LIMIT = 100


class LangfuseScores:
    """Langfuse 写入面:逐条 ``create_score``(锚 = trace_id;dataset run 随 metadata)。"""

    def __init__(self, client: Any) -> None:
        self._client = client

    def write(self, record: ScoreRecord) -> None:
        self._client.create_score(
            score_id=record.score_id,  # 确定性 id:同一条分数重评即覆盖,不叠层
            name=record.name,
            value=record.value,
            trace_id=record.trace_id,
            comment=record.comment,
            metadata=self._metadata(record),
        )

    def _metadata(self, record: ScoreRecord) -> dict:
        """分数 metadata:编排给的原样带上,另补 dataset run id(锚只能给一个,它走这里)。"""
        if record.dataset_run_id is None:
            return record.metadata
        return {**record.metadata, "dataset_run_id": record.dataset_run_id}

    def flush(self) -> None:
        """冲掉 SDK 缓冲(写完必须调,否则最近写入可能还没上报)。"""
        self._client.flush()

    def verify(
        self,
        records: Sequence[ScoreRecord],
        *,
        sleep: Callable[[float], None] = time.sleep,
        attempts: int = VERIFY_ATTEMPTS,
    ) -> None:
        """flush 后从服务端读回,核实每条分数都在(名字 + 值 + **挂对了 trace**)。

        按 ``trace_id`` 逐条查(锚所在,也是读回最省的路);同名分数在换 rubric 重评后恒覆盖同一条
        (确定性 ``score_id``),但在**别的 trace** 上可能有同名分(同场景另一次 run)——故核对键带
        上 trace_id,不能只认名字。读回查不齐即报错,不把「大概写进去了」当写进去了。
        """
        expected = {(record.trace_id, record.name, float(record.value)) for record in records}
        missing: set[tuple[str, str, float]] = expected
        for attempt in range(attempts):
            found = {(getattr(item, "trace_id", None), item.name, float(item.value)) for item in self._fetch(records)}
            missing = expected - found
            if not missing:
                return
            if attempt + 1 < attempts:
                sleep(VERIFY_INTERVAL_SECONDS)
        raise RuntimeError(
            f"分数读回不齐(读回键 trace_id x{len({record.trace_id for record in records})} 条):"
            f"缺 {sorted(missing)}——写入未生效或 ingestion 延迟超限,登 3001 核对 Langfuse"
        )

    def _fetch(self, records: Sequence[ScoreRecord]) -> list[Any]:
        found: list[Any] = []
        for trace_id in dict.fromkeys(record.trace_id for record in records):
            found.extend(self._client.api.scores.get_many(trace_id=trace_id, limit=_READBACK_LIMIT).data)
        return found
