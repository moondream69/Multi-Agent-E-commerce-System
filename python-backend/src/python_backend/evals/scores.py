"""Langfuse 分数写入面(spec #55 B/C / 票 #59):分数挂任务 trace,带 dataset run 与语料锚。

与 ``projection.py`` 同一条注入面:client 可注入(测试用记录式假件,不触真网);缺密钥的闸在
``load_langfuse_config``(CLI 在建连前就过闸),此处不重复检查。

**锚只给一个**(实测,2026-09-15):Langfuse ingestion 对 score-create 事件收
「``traceId`` / ``sessionId`` / ``datasetRunId`` 三选一」——同发 ``traceId`` + ``datasetRunId``
被 400 拒(高层 SDK 的 ``create_score`` 签名允许两者同传,是签名的宽,不是服务的允)。
取舍:锚取 ``trace_id``(分数进任务 trace 的 Scores 面板,「从分数跳到 trace 看全过程」是主用途);
``dataset_run_id`` 经 **metadata** 随带(按 dataset run 归组、可查)。确定性 ``score_id`` 由
「稳定键 + run + trace + 判据文案 + judge 型号」派生(``scoring.py`` 的 ``ScoreRecord.score_id``)。

**同 id 的写入是「就地更新」,且 ``trace_id`` 不跟着走**(2026-09-16 实测;写成「幂等覆盖」或
「静默丢弃」都不准):langfuse ingestion 只有 ``score-create`` 事件(全 SDK 无 ``score-update``),
事件带一个**已存在**的 ``score_id`` 时,name / comment / value / metadata 覆盖到那行既有记录上,
而 **``trace_id`` 与 ``timestamp`` 保持原值**——分数不会迁到这次给的 trace。

实测样本两条:①向 trace ``aaaa…03`` 写 ``score_id=eval-43796a09…``(原属 ``0bb66308…``),结果是
``0bb66308…`` 那行的 name/comment 被换成本次的值,``aaaa…03`` 下查无此行;②``coffee-maker-us#1#1``
挂在旧 trace ``2233261e…`` 上的那行,metadata 里的 ``run_name`` 被后来的跑批改写成了新 run 名。

**为什么要命**:本仓 ``ScoreRecord.score_id`` 原本只由稳定键派生,不含 run/trace。任务线每 run 换
trace,于是重评的分数全被就地更新到**上一次 run 的 trace** 上,新 trace 一条不剩——实测 33 条全落空、
读回全军覆没。故 id 的材料必须把「换 run / 改 rubric / 换 judge」都编进标识里;真需要抹掉某条历史分,
删分不是出路(级联删除陷阱,见 docs/handoffs 记档)。

**读回核对键带 ``score_id``**(不只 trace + name + value):只有「**这一次要写的那条** id 在不在」
问得清身份——工作台线的 trace 跨 run 恒同,写入若真失败,一条同名的旧行就能顶包,只认 name/value
会把「没写进去」读成「写进去了」。
★ 待维护者确认:若 dataset run 面板的关联优先于 trace 面板,把锚换成 ``dataset_run_id`` 即可(一行),
代价是分数不再挂 trace。

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
            score_id=record.score_id,  # 确定性 id:同口径重评就地更新同一条;换 run/rubric/judge 另落一条
            name=record.name,
            value=record.value,
            trace_id=record.trace_id,
            comment=record.comment,
            metadata=self._metadata(record),
        )

    def _metadata(self, record: ScoreRecord) -> dict:
        """分数 metadata:编排给的原样带上,另补 dataset run id(锚只能给一个,它走这里)。

        ``judge_model`` 非空时同样随带:换 judge 会换分数,不记型号就无法按版本读历史
        (机械线无 judge,如实不写)。
        """
        extra = {}
        if record.dataset_run_id is not None:
            extra["dataset_run_id"] = record.dataset_run_id
        if record.judge_model:
            extra["judge_model"] = record.judge_model
        return {**record.metadata, **extra} if extra else record.metadata

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
        """flush 后从服务端读回,核实每条分数都在(**这一次要写的那条真的在**)。

        按 ``trace_id`` 逐条查(锚所在,也是读回最省的路)。核对键 = ``(trace_id, score_id, value)``
        ——**``score_id`` 必须进键**:只认 ``(trace_id, name, value)`` 时,工作台线(其 trace 跨 run
        恒同)上一条**同名的旧行**就能顶包,写入若真失败会被读成「写进去了」。带上 id 才问得清
        「这一次要写的那条在不在」。
        值一并进键:同 id 的写入是**就地更新**(见模块文档),若写入没生效,旧值会如实报缺。
        读回查不齐即报错,不把「大概写进去了」当写进去了。
        """
        expected = {(record.trace_id, record.score_id, float(record.value)) for record in records}
        names = {record.score_id: record.name for record in records}
        missing: set[tuple[str, str, float]] = expected
        for attempt in range(attempts):
            found = {
                (getattr(item, "trace_id", None), getattr(item, "id", None), float(item.value))
                for item in self._fetch(records)
            }
            missing = expected - found
            if not missing:
                return
            if attempt + 1 < attempts:
                sleep(VERIFY_INTERVAL_SECONDS)
        detail = "、".join(sorted(f"{names[score_id]}({trace_id[:8]}…)" for trace_id, score_id, _ in missing))
        raise RuntimeError(
            f"分数读回不齐(读回键 trace_id x{len({record.trace_id for record in records})} 条):"
            f"缺 {detail}——写入未生效(id 撞车被丢弃?)或 ingestion 延迟超限,登 3001 核对 Langfuse"
        )

    def _fetch(self, records: Sequence[ScoreRecord]) -> list[Any]:
        found: list[Any] = []
        for trace_id in dict.fromkeys(record.trace_id for record in records):
            found.extend(self._client.api.scores.get_many(trace_id=trace_id, limit=_READBACK_LIMIT).data)
        return found
