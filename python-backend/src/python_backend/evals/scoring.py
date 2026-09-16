"""``score`` 的编排(spec #55 B/C / 票 #59;规划切片面 #61):已存快照 → 机械防伪引 + judge 判据 →
分数落 Langfuse。

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
**规划切片面不走这条配对**:该面判分对象是**整份计划**(快照 ``plan`` 段),判据三条一次全判
(依赖声明 / 划分 / 领域路由彼此不是「一片一条」的关系),切片号段用常量 ``PLAN_SLICE_KEY``。

分数形状(落 Langfuse 的键与互链):

- **稳定键 = ``<场景id>#<切片号>#<判据序号>``**(机械线为 ``…#机械``,规划面为 ``…#plan#…``):
  改判据文案不改键,换 rubric 重评后历史分数不断成两条线(键形状的定义在 ``schema.py``——
  切片号段是回评面补的,见其 docstring)。
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
from python_backend.evals.schema import PLANNING_SURFACE, Scenario
from python_backend.evals.snapshot import Snapshot, plan_lines, read_snapshot
from python_backend.infrastructure.tracing import eval_root_trace_id

# 机械防伪引在分数稳定键里的段名(判据序号段的并列物):零 LLM、全量跑、不设开关(ADR-0008)
MECHANICAL_KEY = "机械"
# 规划切片面在稳定键里的切片号段(票 #61):判分对象是**整份计划**而非某个切片,故用固定段名
# (同工作台线恒用切片号 1 的做法——切片号是标识不是下标)
PLAN_SLICE_KEY = "plan"


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

    **规划切片面**(票 #61)走另一条:判分对象是快照随带的 ``plan`` 段(整份计划),三条判据一次全判,
    不带机械线(计划没有答案文本、没有引用载荷,机械线在那里的「不适用」不是信息)。空切片即报错
    (跑批不落空快照,快照无产出等于无可评对象);四个产出面都已在跑批器落地,不存在「未实现即跳过」的面。
    """
    if scenario.surface == PLANNING_SURFACE:
        return _plan_records(snapshot, scenario, judge)
    if not snapshot.slices:
        raise RuntimeError(f"快照 {snapshot.scenario_id} 没有切片产出——无可评对象(跑批不落空快照)")
    records: list[ScoreRecord] = []
    criteria_for = _criteria_pairing(scenario.rubric, len(snapshot.slices))
    for position, item in enumerate(snapshot.slices):  # 切片号是标识不是下标,配对按切片在快照里的次序
        answer = item.answer or ""
        records.extend(_mechanical_records(snapshot, str(item.no), answer, item.citations))
        criteria, indexes = criteria_for[position]
        records.extend(_judge_records(snapshot, item.no, scenario, answer, item.citations, criteria, indexes, judge))
    return tuple(records)


def _plan_records(snapshot: Snapshot, scenario: Scenario, judge: JudgeSession) -> tuple[ScoreRecord, ...]:
    """规划切片面:整份计划 → 三条判据一次判(依赖声明 / 划分 / 领域路由)。

    切片计划缺席即报错:该面的场景必须由任务线跑出 ``plan`` 段(跑批时的任务详情随带),快照里没有
    就是跑批没带上——**报错不猜**(空计划判出来的分会把「没评到」洗成一条判定)。判据是**整份**计划的
    判据(不逐片配对):「依赖声明与先序一致」看的是片与片之间的关系,拆片判会把这一半信息切掉。
    """
    if not snapshot.plan:
        raise RuntimeError(
            f"快照 {snapshot.scenario_id} 没有规划段(plan 为空)——规划切片面评的就是它:"
            "该场景须走任务线跑批(task 详情的 plan 段随快照落盘)"
        )
    scores = judge(
        JudgeRequest(
            scenario_id=snapshot.scenario_id,
            slice_no=0,  # 整份计划不是某一「片」:0 只进 judge 的报错定位文案,不进稳定键
            input=scenario.input,
            answer="",
            citations=(),
            criteria=scenario.rubric,
            plan="\n".join(plan_lines(snapshot.plan)),
        )
    )
    # 稳定键的段名取 PLAN_SLICE_KEY;判据序号即判据在真源 rubric 里的位置(请求内位置与它同值)
    return _record_scores(
        snapshot,
        PLAN_SLICE_KEY,
        scores=scores,
        suffix_of=lambda score: str(score.index),
        criterion=lambda score: scenario.rubric[score.index - 1],
        judge_model=judge.model,
    )


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
    snapshot: Snapshot, slice_no: str, answer: str, citations: tuple[dict, ...]
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
    """judge 判据:一次请求发全部判据,逐条收 0/1 + comment(坏输出在 judge 层即报错)。

    ``slice_no`` 在本层是 ``int``(切片号,进 ``JudgeRequest`` 的定位文案);落分时转成键里的段名
    (``_record`` 收 ``str``)。``indexes`` 是**1 起的判据序号**(见 ``_criteria_pairing``):该号既是
    键里的段名、也是**真源 rubric 里的位置**——本条判据的文案取自真源(本片只带一条时,请求里的
    ``criteria`` 是子集,拿它取文案会错位)。
    """
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

    def criterion_of(score: RubricScore) -> str:
        return scenario.rubric[indexes[score.index - 1] - 1]

    return _record_scores(
        snapshot,
        str(slice_no),
        scores=scores,
        suffix_of=lambda score: str(indexes[score.index - 1]),  # 键里是**判据序号**(真源位置),非请求内位置
        criterion=criterion_of,
        judge_model=judge.model,
    )


def _record_scores(
    snapshot: Snapshot,
    slice_no: str,
    *,
    scores: Sequence[RubricScore],
    suffix_of: Callable[[RubricScore], str],
    criterion: Callable[[RubricScore], str],
    judge_model: str,
) -> tuple[ScoreRecord, ...]:
    """逐条判定 → 分数记录(**两条判定线共用的组装配法**):键段 = 切片号/``plan`` + 判据序号。

    ``suffix_of`` / ``criterion`` 是两条线各自的序号映射(逐片判要经配对映射:键取**判据在真源里的
    序号**、文案同样回真源取;整份计划判直接用序号)——**组装与取值分开**,同一套落分写法不为面复制一份。
    """
    return tuple(
        _record(
            snapshot,
            slice_no,
            suffix=suffix_of(score),
            value=1 if score.passed else 0,
            comment=score.comment,
            criterion=criterion(score),
            judge_model=judge_model,
        )
        for score in scores
    )


def _record(
    snapshot: Snapshot,
    slice_no: str,
    *,
    suffix: str,
    value: int,
    comment: str,
    criterion: str,
    judge_model: str = "",
) -> ScoreRecord:
    """一条分数记录:稳定键 + 互链(trace / dataset run)+ 语料版本锚(主锚 + 附记)。

    ``slice_no`` 是**段名**不是数字:切片产出给切片号(如 ``"2"``),规划面给 ``PLAN_SLICE_KEY``
    ——键拼的是文本段,类型本就不必是 int(改名只为如实,行为不变)。

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
