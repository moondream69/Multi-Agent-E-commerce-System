"""产出快照(spec #55 B / 票 #58):跑批的**本地真源**——``score`` 只读快照回评,不重跑任务。

一条场景一次运行 = 一个 JSON 文件(``<快照目录>/<run 名>/<场景 id>.json``):场景 id / thread_id /
trace_id / **切片级产出**(answer / citations / executed / evidence / hits)/ **规划段**(plan,票 #61)/
语料版本锚 / 时间。目录由调用方给定(CLI 默认 ``docs/evals/runs/``,gitignore)。

**切片级保真,不拼顶层长文**:citations 编号是**切片内**编号(``build_citations`` 每片各自从 1 排),
把多片答案拼成长文会让机械防伪引(#57)的编号集跨片串味——A 片幻觉的 ``[2]`` 撞上 B 片合法的 ``[2]``
就洗成了合法引用(假通过)。故快照保持 ``slices[]`` 分组,回评逐片判。

**规划段随任务线产出一起落盘**(票 #61):规划切片场景评的就是它——plan 不是单独跑出来的,是**同一次
任务跑批**的规划产物(暂存假设 5:不另设「只规划」捷径)。任务线快照一律随带(零分叉:同一条产出线的
快照同一种形状,不给「要不要带」加开关);工作台线无任务轨迹,该字段如实留空。

语料版本锚两种(ADR-0008):``corpus_fingerprint`` 为主锚(真源内容哈希,离线可重算、不受净库重建
影响)、``corpus_batch_id`` 为尽力附记(台账读不到即 null,如实标注);``dataset_run_id`` 由投影回填,
供 #59 的分数与 dataset run 互链。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# 快照 schema 版本:字段增删即升版(读旧快照时报错清晰,不静默按新形状解释)。
# **5**(#75 B):切片增 ``hits``(本片**检索命中池**的切块标识,只落 id)——快照原先只带**被引池**
# (``citations`` 由答案里出现过的标记构建),「检索到了但没标引用」与「凭自身记忆补写」在数据上
# 不可区分(2026-09-17 批 `smart-band-us#3#2` 的分诊卡在这里)。
# **4**(#67):切片增 ``evidence``(工作台线的查证证据块)——判据②③要核的商品/订单事实
# 原先不在判分材料里,商品类结论**结构性不可核验**(实评实录见 issue #67)。
# **3**(#64 A3):切片增 ``incomplete``(未完成原因)——三态可辨(B30④)。
# **2**(票 #61):随带 ``plan`` 规划段——规划切片场景的判分对象。
# **1** → 2 的断代是**有意**的:版本闸拒读旧快照,而旧 run 的快照重跑一次即可(产出快照是
# 一次性产物,不承担历史可比性——历史分数归 Langfuse,不归本地 JSON)。
# ⚠️ 升版前先想清**次序**(#64 spec 的教训):判分材料修正后的「现批重评」必须**先于**升版跑完,
# 版本闸一旦抬起,旧版快照就读不动了。3 → 4 无此顾虑:material 修正本身要求重跑(旧快照里
# 没有 evidence 这份数据,重评也核不出商品事实)。4 → 5 同理:``hits`` 是**新数据**,旧快照
# 重评也变不出来(判分材料本身不消费它,故 4 → 5 不影响既有材料)。
SNAPSHOT_VERSION = 5


@dataclass(frozen=True)
class SliceOutput:
    """一个切片的产品面产出(``results`` 里的一条,字段与 graph.py 的 run_output 对齐)。

    ``incomplete``(#64 A3):该切片**未完成**的原因(步数超限 / 子图 LLM 失败 / 作答轮正文为空),
    ``None`` 即「跑完了」。它与 ``answer is None`` 联用才有意义——三态靠这两个字段分开:
    没执行(``executed=False``)/ 空产出(``executed=True`` 且两者皆空)/ 未完成(``incomplete`` 有值)。
    没有它时,「没执行」与「跑了但空产出」在快照里同形,读快照的人会误判。

    ``evidence``(#67 工作台线 / #69 任务线):该切片的**查证证据块**。工作台线是起草线端点随草稿
    返回的那份原样载荷(FAQ 命中 / 订单 / 商品 + 截断标志);任务线是系统记录类查证
    (``order_lookup`` / ``product_lookup`` / ``list_orders``)的逐调用**查回结果**。两线都没有
    这份载荷时如实 ``None``(判分材料随之不渲染该段)。判据②③核「不编造 / 无凭空论断」要的正是它。

    ``hits``(#75 B):该切片的**检索命中池**(切块标识,去重保序,只落 id 不落正文)。``citations``
    是**被引池**(只收答案里出现过标记的切块)⇒ 两者之差 = 「命中了但没标引用」;没有这份清单时,
    它与「凭自身记忆补写」在快照上同形(2026-09-17 批 `smart-band-us#3#2` 的分诊即卡在这里)。
    没有检索轨迹的线(工作台线走端点载荷)如实为空元组。
    """

    no: int
    agent: str
    description: str
    answer: str | None
    citations: tuple[dict, ...]
    executed: bool
    incomplete: str | None = None
    evidence: dict | None = None
    hits: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlicePlanOutput:
    """一条切片的**规划声明**(``GET /api/tasks/{thread_id}`` 的 plan 段里的一条;票 #61)。

    名字带 Output 以别于 ``core/planning.Slice``(那是规划器内部对象)——本类是 API 响应的载荷投影。
    """

    no: int
    agent: str
    description: str
    depends_on: tuple[int, ...]
    approval_points: tuple[str, ...]


@dataclass(frozen=True)
class Snapshot:
    """一条场景的一次运行(快照文件名 = ``<scenario_id>.json``)。

    ``thread_id``:任务线 = 任务线程 id;工作台线(无任务轨迹的同步端点)留**空串**——
    该字段是必填的 ``str``(改可空即改 schema、旧快照读不动),空串即「该线没有线程」的如实标注。

    ``plan``:同任务跑批的规划段(任务线一律随带;工作台线无任务轨迹即空元组)——规划切片场景
    评的就是它(票 #61),其余线的产出不消费它,如实带着不另设开关。
    """

    scenario_id: str
    surface: str
    run_name: str
    thread_id: str
    trace_id: str
    status: str
    slices: tuple[SliceOutput, ...]
    plan: tuple[SlicePlanOutput, ...]  # 同任务跑批的规划段(#61);工作台线无任务轨迹即空
    corpus_fingerprint: str
    corpus_batch_id: str | None  # 摄入台账附记:读不到即 None(如实标注,不编)
    dataset_run_id: str | None  # Langfuse dataset run 回填(#59 落分互链);投影未成功即 None
    recorded_at: datetime


def slices_from_results(results: object) -> tuple[SliceOutput, ...]:
    """``GET /api/tasks/{thread_id}`` 的 ``results``(切片号 → 产出)→ 切片列表(按切片号升序)。

    形状不符(条目不是映射 / 切片号不是整数)即报错——那是 API 契约违反而非「没产出」;
    空/缺席(线程未跑、checkpointer 无状态)如实返回空列表,由调用方判「无可评产出」。
    """
    if results is None:
        return ()
    if not isinstance(results, dict):
        raise ValueError(f"results 须是「切片号 → 产出」映射,实际是 {type(results).__name__}")
    slices: list[SliceOutput] = []
    for key, entry in results.items():
        if not isinstance(entry, dict):
            raise ValueError(f"切片 {key} 的产出不是映射(实际 {type(entry).__name__})")
        try:
            number = int(key)
        except (TypeError, ValueError) as error:
            raise ValueError(f"切片号 {key!r} 不是整数") from error
        slices.append(
            SliceOutput(
                no=number,
                agent=str(entry.get("agent", "")),
                description=str(entry.get("description", "")),
                answer=entry.get("answer"),
                citations=tuple(entry.get("citations") or ()),
                executed=bool(entry.get("executed")),
                incomplete=_optional_str(entry.get("incomplete")),
                # #69:任务线的系统记录查证块随 results 下发(工作台线由 runner 直接构造,不走这里)
                evidence=_optional_dict(entry.get("evidence")),
                # #75 B:本片检索命中池(未引用的命中也在;缺省即空——没有检索轨迹的产出不编)
                hits=_chunk_ids(entry.get("hits")),
            )
        )
    return tuple(sorted(slices, key=lambda item: item.no))


def slices_from_plan(plan: object) -> tuple[SlicePlanOutput, ...]:
    """``GET /api/tasks/{thread_id}`` 的 ``plan``(切片计划载荷)→ 规划切片列表(按切片号升序)。

    缺席(``None``/空)如实返回空元组——工作台线没有规划段,由调用方判「该线不该有」还是「没跑出计划」;
    形状不符(条目不是映射 / 切片号不是整数 / depends_on 不是整数清单)即报错:那是 API 契约违反。
    """
    if plan is None:
        return ()
    if not isinstance(plan, dict):
        raise ValueError(f"plan 须是「slices 清单」映射,实际是 {type(plan).__name__}")
    entries = plan.get("slices")
    if not isinstance(entries, list):
        raise ValueError("plan.slices 须是清单")
    slices: list[SlicePlanOutput] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"plan 的切片不是映射(实际 {type(entry).__name__})")
        depends_on = entry.get("depends_on") or []
        if not isinstance(depends_on, list) or not all(isinstance(item, int) for item in depends_on):
            raise ValueError(f"plan 切片 depends_on 须是整数清单:{depends_on!r}")
        slices.append(
            SlicePlanOutput(
                no=_plan_integer(entry.get("no")),
                agent=str(entry.get("agent", "")),
                description=str(entry.get("description", "")),
                depends_on=tuple(depends_on),
                approval_points=tuple(str(point) for point in entry.get("approval_points") or ()),
            )
        )
    return tuple(sorted(slices, key=lambda item: item.no))


def plan_lines(plan: tuple[SlicePlanOutput, ...]) -> list[str]:
    """规划段 → 判据提示词里的可读行(依赖声明 / 审批点如实呈现,缺即标「无」)。

    **不在这里替 judge 判断合理性**(「划分是否合理」是判据的活),也不各自抄一份计划拼法:
    ``SlicePlanOutput`` 的定义处即它的渲染处(判分面只收已成形文本)。
    """
    lines = [f"(共 {len(plan)} 片)"]
    lines.extend(
        f"切片 {item.no}:业务域 {item.agent} | 说明:{item.description}"
        f" | 依赖:{_number_list(item.depends_on)} | 审批点:{'、'.join(item.approval_points) or '无'}"
        for item in plan
    )
    return lines


def _number_list(numbers: tuple[int, ...]) -> str:
    return "、".join(str(number) for number in numbers) if numbers else "无"


def _plan_integer(value: Any) -> int:
    """plan 条目的切片号:非整数即报错(不 ``int()`` 硬转——那会把 ``"x"`` 崩成栈)。"""
    if not isinstance(value, int):
        raise ValueError(f"plan 的切片号不是整数:{value!r}")
    return value


def snapshot_path(run_dir: Path, scenario_id: str) -> Path:
    """快照文件路径 = ``<run 目录>/<场景 id>.json``(场景 id 全局唯一,即文件名的唯一性来源)。"""
    return run_dir / f"{scenario_id}.json"


def write_snapshot(run_dir: Path, snapshot: Snapshot) -> Path:
    """落盘快照(目录不存在即建);返回写入路径。同路径重复写即覆盖(重跑同场景即更新产出)。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(run_dir, snapshot.scenario_id)
    path.write_text(json.dumps(_payload(snapshot), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_snapshot(path: Path) -> Snapshot:
    """读快照 → 快照对象;形状非法即报错(报错须指明**哪个文件、哪个字段**)。

    ``plan`` 字段缺席读成空(工作台线**当前版本**的快照本就不带这一项:``slices_from_plan`` 对
    空载荷返回空元组)——这是**同一版本内**的合法留空,与「版本闸拒读**旧版**文件」是两回事:
    前者是字段级容错,后者是断代(旧 run 重跑一次即可)。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}:不是合法 JSON({error})") from error
    if not isinstance(raw, dict):
        raise ValueError(f"{path}:顶层须是映射,不是 {type(raw).__name__}")
    version = raw.get("version")
    if version != SNAPSHOT_VERSION:
        raise ValueError(f"{path}:快照版本 {version!r} 不受支持(本实现只读 version={SNAPSHOT_VERSION})")
    required = ("scenario_id", "surface", "run_name", "thread_id", "trace_id", "status", "recorded_at")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError(f"{path}:缺字段 {'、'.join(missing)}")
    entries = raw.get("slices")
    if not isinstance(entries, list):
        raise ValueError(f"{path}:slices 须是清单")
    try:
        recorded_at = datetime.fromisoformat(str(raw["recorded_at"]))
    except ValueError as error:
        raise ValueError(f"{path}:recorded_at 不是 ISO 8601 时刻({raw['recorded_at']!r})") from error
    return Snapshot(
        scenario_id=str(raw["scenario_id"]),
        surface=str(raw["surface"]),
        run_name=str(raw["run_name"]),
        thread_id=str(raw["thread_id"]),
        trace_id=str(raw["trace_id"]),
        status=str(raw["status"]),
        slices=tuple(_slice_from_json(entry, path) for entry in entries),
        plan=_plan_from_json(raw.get("plan"), path),
        corpus_fingerprint=str(raw.get("corpus_fingerprint", "")),
        corpus_batch_id=_optional_str(raw.get("corpus_batch_id")),
        dataset_run_id=_optional_str(raw.get("dataset_run_id")),
        recorded_at=recorded_at,
    )


def _plan_from_json(raw: Any, path: Path) -> tuple[SlicePlanOutput, ...]:
    """快照里的规划段 → 规划切片列表;形状坏掉即报错点名文件(``slices_from_plan`` 同一套规则)。"""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{path}:plan 须是清单(切片计划载荷的切片数组)")
    try:
        return slices_from_plan({"slices": raw})
    except ValueError as error:
        raise ValueError(f"{path}:{error}") from error


def _slice_from_json(entry: Any, path: Path) -> SliceOutput:
    if not isinstance(entry, dict):
        raise ValueError(f"{path}:slices 的条目须是映射,不是 {type(entry).__name__}")
    citations = entry.get("citations") or []
    if not isinstance(citations, list):
        raise ValueError(f"{path}:切片 {entry.get('no')} 的 citations 须是清单")
    return SliceOutput(
        no=int(entry["no"]),
        agent=str(entry.get("agent", "")),
        description=str(entry.get("description", "")),
        answer=entry.get("answer"),
        citations=tuple(citations),
        executed=bool(entry.get("executed")),
        incomplete=_optional_str(entry.get("incomplete")),
        evidence=_optional_dict(entry.get("evidence")),
        hits=_chunk_ids(entry.get("hits")),
    )


def _chunk_ids(value: Any) -> tuple[str, ...]:
    """切块标识清单(``hits``,#75 B)字段级容错:缺席/空即空元组;形状坏掉即报错,不当空处理。

    与 ``evidence`` 同一姿势——**同一版本内**的合法留空(工作台线无检索轨迹、旧形状的 API 载荷)
    vs 「有清单但形状不对」是两回事,后者是契约违反,报错不静默。
    """
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"hits 须是切块标识字符串清单,实际是 {value!r}")
    return tuple(value)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_dict(value: Any) -> dict | None:
    """可缺席的映射字段(evidence,#67):缺席即 None(任务线如实没有);形状坏掉即报错,不当空处理。"""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"evidence 须是映射(证据块载荷),不是 {type(value).__name__}")
    return value


def _payload(snapshot: Snapshot) -> dict:
    """快照对象 → JSON 载荷(字段名即 schema;改这里等于改 schema,与 SNAPSHOT_VERSION 同步)。"""
    return {
        "version": SNAPSHOT_VERSION,
        "scenario_id": snapshot.scenario_id,
        "surface": snapshot.surface,
        "run_name": snapshot.run_name,
        "thread_id": snapshot.thread_id,
        "trace_id": snapshot.trace_id,
        "status": snapshot.status,
        "slices": [
            {
                "no": item.no,
                "agent": item.agent,
                "description": item.description,
                "answer": item.answer,
                "citations": list(item.citations),
                "executed": item.executed,
                "incomplete": item.incomplete,
                "evidence": item.evidence,
                "hits": list(item.hits),
            }
            for item in snapshot.slices
        ],
        "plan": [
            {
                "no": item.no,
                "agent": item.agent,
                "description": item.description,
                "depends_on": list(item.depends_on),
                "approval_points": list(item.approval_points),
            }
            for item in snapshot.plan
        ],
        "corpus_fingerprint": snapshot.corpus_fingerprint,
        "corpus_batch_id": snapshot.corpus_batch_id,
        "dataset_run_id": snapshot.dataset_run_id,
        "recorded_at": snapshot.recorded_at.isoformat(),
    }
