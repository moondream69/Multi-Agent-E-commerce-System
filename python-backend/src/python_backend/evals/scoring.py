"""``score`` 的编排(spec #55 B/C / 票 #59):已存快照 → 机械防伪引 + judge 判据 → 分数落 Langfuse。

**快照是唯一输入**(ADR-0008 的 run/score 解耦):评分不驱动任何任务,只读 ``docs/evals/runs/<run 名>/``
下的 JSON——换 rubric、换 judge 只烧 judge token,任务不被重跑。rubric 从**真源 YAML 现读**,不取
dataset item metadata 里的副本:改 rubric 才算「换 rubric」,取副本就把真源绕过去了。

判定两条线,独立并存(不互相遮蔽):

1. **机械防伪引**(零 LLM,``core/citations.py`` 的唯一解析点):逐切片判「标记 ⇄ 载荷一一对应」
   ——带标记或载荷的产出出 0/1,**两者皆无判不适用**(不落分,也不作通过计)。
   传 ``hits=None``:快照不随带本轮命中集(T1/T2 的接口决定,偏差已披露于 #55),故
   「载荷条目与命中集不相交」一式在此不可判——残留未解析标记(疑似伪造编号)照判。
2. **judge 判据**:逐切片、逐条 rubric 判 0/1 + comment。切片级保真(不拼多片长文,citations
   编号是切片内编号——跨片拼文会让编号集串味成假通过,与 ``snapshot.py`` 同一条口径)。

判据与切片的配对:条数相等 ⇒ 一条判据对一片(rubric 即按片写的);不等 ⇒ 每条判据对每片
(广播)——**配对规则本身即口径**,改动它等于改动历史分数的含义,故写在这里而非常量外置。

分数形状(落 Langfuse 的键与互链):

- **稳定键 = ``<场景id>#<切片号>#<判据序号>``**(机械线为 ``…#机械``):改判据文案不改键,换
  rubric 重评后历史分数不断成两条线(键形状的定义在 ``schema.py``——切片号段是回评面补的,见其 docstring)。
- 分数挂 ``trace_id``:任务线 = 快照随带的任务 trace(由 thread_id 派生);工作台线挂**跑批器
  自建的评测根 trace** ``eval:<场景id>``(快照 trace_id 即它,id 由 ``eval_root_trace_id`` 确定性派生,
  trace 由跑批器在 run 时建出,#60)。
- 随带 ``dataset_run_id``(快照由投影回填)+ metadata(语料指纹为主锚、批次号为附记)。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_OID, uuid5

from python_backend.core.citations import check_citations
from python_backend.evals.judge import Judge, JudgeRequest, RubricScore
from python_backend.evals.schema import Scenario
from python_backend.evals.snapshot import Snapshot, read_snapshot

# 机械防伪引在分数稳定键里的段名(判据序号段的并列物):零 LLM、全量跑、不设开关(ADR-0008)
MECHANICAL_KEY = "机械"


def eval_root_trace_name(scenario_id: str) -> str:
    """工作台线评测根 trace 的**可读标记**(trace 名 = 该名字;id 是它的确定性派生)。

    跑批器建 trace(``projection.create_eval_trace``)与回评读分数两处共用——原名与 id 拆开,
    改名不悄悄改 id(两处须恒等,见 ``eval_root_trace_id``)。
    """
    return f"eval:{scenario_id}"


def eval_root_trace_id(scenario_id: str) -> str:
    """工作台线的**评测根 trace** id:由场景 id 确定性派生(与 ``task_trace_id`` 同法)。

    工作台线(起草台)没有任务轨迹,分数得挂在跑批器自建的评测根 trace 上(ADR-0008);
    任务线用快照随带的任务 trace,**只有缺 trace 的快照**走这里——两条线共用一套分数形状。
    langfuse 的 trace_id 契约是 32 位小写十六进制(4.x ``_is_valid_trace_id``),故取
    ``uuid5(NAMESPACE_OID, …)`` 的 hex;可读标记留给 trace 名(``eval_root_trace_name``,run 时落,#60)。
    """
    return uuid5(NAMESPACE_OID, eval_root_trace_name(scenario_id)).hex


@dataclass(frozen=True)
class ScoreRecord:
    """一条待落的分数:稳定键 + 值(0/1)+ 理由 + 互链(trace / dataset run)+ 语料版本锚。

    ``score_id`` 由稳定键确定性派生(同一场景/切片/判据恒同一 id):换 rubric 重评**覆盖同一
    条分数**而不是叠一层,历史不因重评堆成两条线(与稳定键同一目的)。
    """

    name: str
    value: int
    comment: str
    trace_id: str
    dataset_run_id: str | None
    metadata: dict
    judge_model: str = ""  # judge 判据线所判型号(机械线为空):换 judge 即换分数,型号随分落库

    @property
    def score_id(self) -> str:
        """落库 id:``<稳定键>`` 的确定性派生(langfuse 对同 id 幂等覆盖)。"""
        return f"eval-{uuid5(NAMESPACE_OID, self.name).hex}"


@dataclass(frozen=True)
class JudgeSession:
    """一次回评的判据会话:judge 客户端 + **所判型号**(型号随分数落库——换 judge 即换分数,
    不记型号就没法按版本读历史)。

    包一层是为了让「客户端 + 型号」作为**一个对象**穿过编排:否则型号得逐层加参数,而分数记录
    的组装在最后一层(``_record``)。``__call__`` 让会话可直接当 judge 客户端用。
    """

    judge: Judge
    model: str = ""

    def __call__(self, request: JudgeRequest) -> list[RubricScore]:
        return self.judge.judge(request)


class ScoreSink(Protocol):
    """编排依赖的落分面(测试注入记录式假件);真身 = ``evals/scores.py`` 的 ``LangfuseScores``。"""

    def write(self, record: ScoreRecord) -> None: ...


@dataclass(frozen=True)
class ScoringResult:
    """一次回评的**全部落分**(顺序即写入顺序;调用方据此打印汇总)。"""

    records: tuple[ScoreRecord, ...]

    @property
    def passed(self) -> int:
        return sum(1 for record in self.records if record.value == 1)


def load_run_snapshots(run_dir: Path) -> tuple[Snapshot, ...]:
    """跑批目录 → 快照列表(按场景 id 升序);无快照即报错(回评要有可评对象)。

    ``run`` 先落快照再投影,目录里可能残留 ``*.json`` 之外的杂物(如复制来的明文)——只认
    ``*.json``;其中任一份读不动即报错,不静默跳过(少评一条就是漏掉一条场景)。
    """
    if not run_dir.is_dir():
        raise RuntimeError(f"跑批目录不存在:{run_dir}——先跑 `evals.py run`(或用 --run 指一个 run 名/路径)")
    paths = sorted(run_dir.glob("*.json"))
    if not paths:
        raise RuntimeError(f"{run_dir} 下没有快照——先跑 `evals.py run`,或换一个 run")
    return tuple(read_snapshot(path) for path in paths)


def scenario_map(scenarios: Sequence[Scenario], snapshots: Sequence[Snapshot]) -> dict[str, Scenario]:
    """快照的场景 id → 真源场景;快照里的场景在真源中缺席即报错(评的是真源,不是残影)。"""
    by_id = {scenario.id: scenario for scenario in scenarios}
    missing = sorted({snapshot.scenario_id for snapshot in snapshots} - set(by_id))
    if missing:
        raise RuntimeError(
            f"快照的场景在评测真源里找不到:{'、'.join(missing)}——"
            "真源被改过或快照来自另一份场景集,先核对 docs/evals/*.yaml"
        )
    return by_id


def score_snapshot(snapshot: Snapshot, scenario: Scenario, judge: JudgeSession) -> tuple[ScoreRecord, ...]:
    """一份快照 → 分数清单:逐切片跑机械线 + judge 线,组装成可落库的记录(不写库)。

    空切片即报错(跑批不落空快照,快照无产出等于无可评对象);未实现的产出面在 CLI 层跳过
    (``evals.py`` 的 ``IMPLEMENTED_SURFACES``,票 #60/#61 铺开),不在此处悄悄漏评。
    """
    if not snapshot.slices:
        raise RuntimeError(f"快照 {snapshot.scenario_id} 没有切片产出——无可评对象(跑批不落空快照)")
    records: list[ScoreRecord] = []
    criteria_for = _criteria_pairing(scenario.rubric, len(snapshot.slices))
    for position, item in enumerate(snapshot.slices):  # 切片号是标识不是下标,配对按切片在快照里的次序
        answer = item.answer or ""
        records.extend(_mechanical_records(snapshot, item.no, answer, item.citations))
        criteria, indexes = criteria_for[position]
        records.extend(_judge_records(snapshot, item.no, scenario, answer, item.citations, criteria, indexes, judge))
    return tuple(records)


def score_run(
    snapshots: Sequence[Snapshot],
    scenarios: Sequence[Scenario],
    *,
    judge: Judge,
    sink: ScoreSink,
    on_record: Callable[[ScoreRecord], None] | None = None,
    judge_model: str = "",
) -> ScoringResult:
    """快照集与真源场景配对后逐条评分并落库;返回全部记录(与写入顺序一致)。

    ``judge_model`` 是**所判型号**(随分数落 metadata,供按 judge 版本读历史);机械线无 judge
    不记。``on_record`` 只作进度回显(一条场景烧一次 judge 调用,CLI 要能报出刚评到哪)。judge
    失败或坏输出经 ``JudgeError`` 抛出:评到一半中止(已落的分是**已判定的那部分**,不是半份判定),
    快照仍在盘上,可原样重跑。
    """
    session = JudgeSession(judge=judge, model=judge_model)
    by_id = scenario_map(scenarios, snapshots)
    records: list[ScoreRecord] = []
    for snapshot in snapshots:
        for record in score_snapshot(snapshot, by_id[snapshot.scenario_id], session):
            sink.write(record)
            records.append(record)
            if on_record is not None:
                on_record(record)
    return ScoringResult(records=tuple(records))


def _criteria_pairing(rubric: tuple[str, ...], slice_count: int) -> list[tuple[tuple[str, ...], tuple[int, ...]]]:
    """判据 ↔ 切片配对 → 逐片 ``(要判的判据, 各判据在真源 rubric 里的序号)``。

    条数相等即一一对应,否则每条判据对每片(广播;见模块 docstring 的口径说明)。序号随带而非
    事后按文案反查:一一对应时同一片只带一条判据,反查会把序号算错位。
    """
    if len(rubric) == slice_count:
        return [((criterion,), (index,)) for index, criterion in enumerate(rubric, start=1)]
    indexes = tuple(range(1, len(rubric) + 1))
    return [(rubric, indexes)] * slice_count


def _mechanical_records(
    snapshot: Snapshot, slice_no: int, answer: str, citations: tuple[dict, ...]
) -> tuple[ScoreRecord, ...]:
    """机械防伪引:applicable=False(既无标记也无载荷)→ 不落分(不适用,不作通过计)。"""
    check = check_citations(answer, list(citations))
    if not check.applicable:
        return ()
    detail = ", ".join(f"{violation.kind}:{violation.detail}" for violation in check.violations)
    return (
        _record(
            snapshot,
            slice_no,
            suffix=MECHANICAL_KEY,
            value=0 if check.violations else 1,
            comment=detail or "引用标记与 citations 载荷一一对应(无残留、无可疑编号)",
            criterion="机械防伪引:答案引用标记 ⇄ citations 载荷一一对应,残留未解析标记 = 疑似伪造",
        ),
    )


def _judge_records(
    snapshot: Snapshot,
    slice_no: int,
    scenario: Scenario,
    answer: str,
    citations: tuple[dict, ...],
    criteria: tuple[str, ...],
    indexes: tuple[int, ...],
    judge: JudgeSession,
) -> tuple[ScoreRecord, ...]:
    """judge 判据:一次请求发全部判据,逐条收 0/1 + comment(坏输出在 judge 层即报错)。"""
    scores = judge(
        JudgeRequest(
            scenario_id=snapshot.scenario_id,
            slice_no=slice_no,
            input=scenario.input,
            answer=answer,
            citations=citations,
            criteria=criteria,
        )
    )
    return tuple(
        _record(
            snapshot,
            slice_no,
            suffix=str(indexes[score.index - 1]),
            value=1 if score.passed else 0,
            comment=score.comment,
            criterion=criteria[score.index - 1],
            judge_model=judge.model,
        )
        for score in scores
    )


def _record(
    snapshot: Snapshot,
    slice_no: int,
    *,
    suffix: str,
    value: int,
    comment: str,
    criterion: str,
    judge_model: str = "",
) -> ScoreRecord:
    """一条分数记录:稳定键 + 互链(trace / dataset run)+ 语料版本锚(主锚 + 附记)。

    ``judge_model`` 由 judge 线带上(机械线留空)——型号入 metadata 是**版本锚的一部分**:
    与语料指纹同理,不记型号的历史分数无从按 judge 版本归因。
    """
    return ScoreRecord(
        name=f"{snapshot.scenario_id}#{slice_no}#{suffix}",
        value=value,
        comment=comment,
        trace_id=snapshot.trace_id or eval_root_trace_id(snapshot.scenario_id),
        dataset_run_id=snapshot.dataset_run_id,
        metadata={
            "scenario_id": snapshot.scenario_id,
            "surface": snapshot.surface,
            "slice_no": slice_no,
            "thread_id": snapshot.thread_id,
            "run_name": snapshot.run_name,
            "corpus_fingerprint": snapshot.corpus_fingerprint,
            "corpus_batch_id": snapshot.corpus_batch_id,
            "criterion": criterion,
        },
        judge_model=judge_model,
    )
