"""judge 面(spec #55 C/D / 票 #59):跨厂判据模型——**只发朴素字段**(messages + max_tokens)。

judge 是**独立于被评对象**的跨厂模型(中转站 Claude),配置三键 ``judge_*`` 与业务 LLM 的
``llm_*`` 分离——换 judge 是配置面操作,不动产品面。走 **Anthropic 原生 Messages API**
(ADR-0008 修订记录:中转站对 ``claude-opus-5`` 只开原生格式),故 ``JUDGE_API_URL`` 按中转站
的 messages 端点给(``…/v1/messages``),客户端取基址时去掉 ``/messages`` 末段。

**只依赖 messages 输入输出**:effort / thinking 等原生参数在中转站的透传程度不一(ADR-0008
边界风险),judge 不依赖其生效——风格与输出约束全部写进提示词。

**坏输出有明确报错姿态、不吞**:判据按行收,行数 / 序号 / 判定值任一对不上即 ``JudgeError``
(带模型原文片段),绝不把解析不到的判定猜成通过——一份「看着成功」的错分比跑失败更坏。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from python_backend.settings import Settings, get_settings

# 判据一次发全部(逐条一次调用会把 token 与延迟按判据条数放大)。
# 这是**朴素字段**(ADR-0008):judge 不依赖任何透传程度不一的原生参数。
#
# **这个预算要罩得住「思考」**(2026-09-16 实测,换 GLM 时撞上):有的模型把 thinking 计入
# ``max_tokens``,且思考长度**不可预测**——1024 时 GLM 把整份预算花在思考上,正文半句都挤不出
# (``stop_reason=max_tokens``、只有一个 thinking 块);实测 64 token 的短探针同样如此。
# 维护者裁决:judge 用量本就不高,**放宽不心疼**,取 16384。放大幅度解决绝大多数请求,但**不是
# 银弹**:个别请求思考更长,表现是**连接中断 / 无文本块**(解析层按设计报错不猜,重试或换池可解)。
# 换 judge 若见这两种报错成片出现,先怀疑这里而不是解析层。
MAX_TOKENS = 16384
# 坏输出报错时随带模型原文的截断长度(证据够定位即可,不把整篇塞进报错)
_RAW_EXCERPT = 300


@dataclass(frozen=True)
class JudgeConfig:
    """judge 端点三键(判分客户端据此建连)。"""

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


def api_base_url(api_url: str) -> str:
    """``JUDGE_API_URL`` → SDK 的 ``base_url``:URL 按中转站的 messages 端点给,末段 ``/messages`` 与
    版本段 ``/v1`` 去掉。

    版本段必须去:SDK 自己拼 ``/v1/messages``(anthropic 1.x 内置版本段),留着就成
    ``/v1/v1/messages``。给基址也能跑(两段都去后即自身)——免得两种写法悄悄打出两个请求路径。
    """
    trimmed = api_url.rstrip("/")
    if trimmed.endswith("/messages"):
        trimmed = trimmed[: -len("/messages")]
    return trimmed[: -len("/v1")] if trimmed.endswith("/v1") else trimmed


class JudgeError(RuntimeError):
    """judge 调用失败或输出不受解析——编排层据此中止(不落半份分)。"""


@dataclass(frozen=True)
class JudgeRequest:
    """一次判据请求:一条场景产出的**全部上下文**(判据清单在内)。

    切片级保真(不拼多片长文):citations 编号是**切片内**编号,跨片拼文会让编号集串味
    (假通过)——与 ``snapshot.py`` 同一条口径。

    ``plan``:规划切片场景的判分对象(票 #61)——渲染好的切片计划文本(``snapshot.plan_lines``)。
    **整段给**,不逐片拆:三条判据(依赖声明 / 划分 / 领域路由)评的就是整份计划,拆片看会把
    「依赖跨片」这一半信息切掉。非规划面如实留 ``""``。
    """

    scenario_id: str
    slice_no: int
    input: str
    answer: str
    citations: tuple[dict, ...]
    criteria: tuple[str, ...]
    plan: str = ""


@dataclass(frozen=True)
class RubricScore:
    """一条判据的判定:0/1(通过/失败)+ 理由(comment)。``index`` = 该判据在本请求里的序号。"""

    index: int
    passed: bool
    comment: str


class Judge(Protocol):
    """编排依赖的判据面(测试注入假件:记录发出去什么、返回固定判定)。"""

    def judge(self, request: JudgeRequest) -> list[RubricScore]: ...


def render_prompt(request: JudgeRequest) -> str:
    """判据请求 → 提示词(风格与输出约束全在此;请求本身仍是朴素 messages)。

    引用条目**只给溯源与切块标识,不给原文**:判「结论是否有检索依据」看的是引用的出处是否
    对得上题目,而答案文本已含锚定的 ``[n]``;塞进全文只会放大 token 且让 judge 转去评文风。

    判分对象二选一:``plan`` 非空 = 规划切片面(评的是整份切片计划,产出段换成【切片计划】);
    否则 = 既有「文本产出 + 引用条目」形状(票 #59/#60 的线原样不动)。
    """
    lines = [
        "你是资深电商选品与客服质量评审。请对下面**一条** Agent 产出逐条判定评分标准是否通过。",
        "",
        "【任务指令】",
        request.input,
        "",
    ]
    if request.plan:
        lines.extend(["【切片计划(Manager 的规划产出:切片划分 + 依赖声明)】", request.plan, ""])
    else:
        lines.extend(
            [
                f"【产出(切片 {request.slice_no})】",
                request.answer,
                "",
                "【该产出的引用条目(编号 → 出处)】",
            ]
        )
        lines.extend(_citation_lines(request.citations))
        lines.append("")
    lines.extend(["【评分标准】"])
    lines.extend(f"{index}. {criterion}" for index, criterion in enumerate(request.criteria, start=1))
    lines.extend(
        [
            "",
            "【判定要求】",
            "- 每条标准独立判定,不受其他标准结果影响;宁可判失败,不要放过没把握的。",
            "- 只依据上面给的材料,不引入你自己的外部知识。",
            "",
            "【输出格式】每条标准输出一行,形如:",
            "<标准序号>|<0 或 1>|<一句话理由>",
            "0 = 未通过,1 = 通过。共 " + str(len(request.criteria)) + " 行,序号自 1 起、与评分标准一一对应;",
            "不要输出任何其他内容。",
        ]
    )
    return "\n".join(lines)


def _citation_lines(citations: tuple[dict, ...]) -> list[str]:
    """引用载荷 → 提示词里的「编号 → 出处」行(空载荷如实标注,不假装有依据)。"""
    if not citations:
        return ["(无——该产出没有引用条目)"]
    lines: list[str] = []
    for entry in citations:
        chunk_ids = "、".join(str(chunk.get("id", "")) for chunk in entry.get("chunks") or [])
        title = entry.get("title") or "(无标题)"
        source = entry.get("source") or "(无来源)"
        lines.append(f"[{entry.get('number')}] {title} — {source};切块:{chunk_ids or '(无)'}")
    return lines


def parse_judgment(text: str, expected: int) -> list[RubricScore]:
    """judge 输出 → 逐条判定;行数 / 序号 / 判定值任一对不上即 ``JudgeError``(带原文片段)。

    按**行**收(序号 | 0/1 | 理由)——行数即判据条数,多判漏判都是坏输出;理由里的 ``|``
    不参与切分(只从左切两刀),剔除空行、容 ``` 围栏。
    """
    outcomes: dict[int, RubricScore] = {}
    for raw_line in _unwrap_fence(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        index, verdict, comment = _parse_line(line, text)
        if index in outcomes:
            raise JudgeError(f"judge 输出第 {index} 条判据给了两行——坏输出:{text[:_RAW_EXCERPT]!r}")
        outcomes[index] = RubricScore(index=index, passed=verdict, comment=comment)

    expected_indexes = set(range(1, expected + 1))
    if set(outcomes) != expected_indexes:
        raise JudgeError(
            f"judge 输出与判据条数对不上(判据 {expected} 条 1..{expected},"
            f"输出 {len(outcomes)} 行 序号 {sorted(outcomes) or '无'}):{text[:_RAW_EXCERPT]!r}"
        )
    return [outcomes[index] for index in sorted(outcomes)]


def _unwrap_fence(text: str) -> str:
    """剥掉整段包裹的 ``` 围栏(模型爱给;裸行与围栏内内容都是合法形状)。"""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    body = stripped[3:]
    body = body.split("\n", 1)[1] if "\n" in body else ""
    return body.rsplit("```", 1)[0] if "```" in body else body


def _parse_line(line: str, text: str) -> tuple[int, bool, str]:
    parts = [part.strip() for part in line.split("|", 2)]
    if len(parts) != 3:
        raise JudgeError(f"judge 输出行不成形状(须「序号|0 或 1|理由」):{line!r} ← 原文 {text[:_RAW_EXCERPT]!r}")
    try:
        index = int(parts[0])
    except ValueError as error:
        raise JudgeError(f"judge 输出的判据序号不是整数:{parts[0]!r} ← 原文 {text[:_RAW_EXCERPT]!r}") from error
    if parts[1] not in ("0", "1"):
        raise JudgeError(f"judge 判定值不是 0/1:{parts[1]!r} ← 原文 {text[:_RAW_EXCERPT]!r}")
    if not parts[2]:
        raise JudgeError(f"judge 未给理由(comment 空):{line!r} ← 原文 {text[:_RAW_EXCERPT]!r}")
    return index, parts[1] == "1", parts[2]


class AnthropicJudge:
    """judge 真身:官方 ``anthropic`` SDK 走**原生 Messages API**,``base_url`` 指中转站。

    ``client`` 可注入(测试传假件或 ``http_client=httpx2.MockTransport`` 的真客户端),
    不注入则按配置建连。SDK 自带重试;调用失败与坏输出统一收敛成 ``JudgeError``——编排层
    只需认这一种错。
    """

    def __init__(self, config: JudgeConfig, client: Any | None = None) -> None:
        self._config = config
        self._client = client if client is not None else _build_client(config)

    def judge(self, request: JudgeRequest) -> list[RubricScore]:
        try:
            response = self._client.messages.create(
                model=self._config.model,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": render_prompt(request)}],
            )
        except Exception as error:  # SDK 类型化错误 / 网络 / 中转站 5xx:收敛成一种错,原样带原因
            raise JudgeError(f"judge 调用失败(场景 {request.scenario_id} 切片 {request.slice_no}):{error}") from error
        return parse_judgment(_text_of(response), expected=len(request.criteria))


def _build_client(config: JudgeConfig) -> Any:
    from anthropic import Anthropic

    return Anthropic(api_key=config.api_key, base_url=api_base_url(config.api_url))


def _text_of(response: Any) -> str:
    """Messages 响应 → 正文;块形状不对(中转给怪东西)即 ``JudgeError``。"""
    blocks = getattr(response, "content", None)
    texts = [getattr(block, "text", None) for block in blocks or [] if getattr(block, "type", None) == "text"]
    joined = "\n".join(text for text in texts if text)
    if not joined.strip():
        raise JudgeError(f"judge 响应没有文本块:{response!r}"[: _RAW_EXCERPT + 60])
    return joined
