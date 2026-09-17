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

import json
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
# 单切块正文进提示词的字符上限 / 引用块总预算(#64 A1 立,**#71 发现二按语料实测抬档**):
# 两级上限让提示词有界,二者超限都**显式标注**(见 _citation_lines)——截断必须是可见事实。
# 上限管的是**被引正文**的长度;标识前缀与截断标注另计(整行计费,见 _chunk_line)。
#
# 600/6000 是 #64 A1 的拍脑袋值,实测**截断 363/608 个被引切块**(语料切块 p50=736、max=1302;
# ``corpus/chunking.py`` 目标 500~800 + 12% 重叠 + 分页打包)——2026-09-17 批把支撑句
# 「$8.4 billion」(块内第 678 字)截在材料外,judge 只见同文档附表行的 8,000 ⇒ 判「与命中不符」。
# 1400 > 语料 max ⇒ 每个切块全文进材料;12000 > 实测单切片被引正文最大 10877 字 ⇒ 总预算
# 也不再让尾块降级为「从略」。两级上限仍在(换语料/换切块参数后照样有界、照样显式标注)。
_CHUNK_EXCERPT = 1400
_CITATIONS_BUDGET = 12000


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

    ``evidence``:该切片的**查证证据块**——两条线两种形状(#67 工作台线 / #69 任务线)。
    工作台线是起草端点原样载荷(FAQ 命中 / 订单 / 商品 + 截断标志);任务线是系统记录类查证
    (``order_lookup`` / ``product_lookup`` / ``list_orders``)的逐调用结果(``{"lookups": [...]}``
    形状,列表型已按上限于出块时裁剪并标注行数)。两线都没有这份载荷时如实 ``None``
    (材料随之不渲染该段,与「有证据块但全空」是两回事)。
    它跟着 ``citations`` 走**原始载荷**而非渲染文本:渲染是判分面自己的事(同引用条目的口径)。
    """

    scenario_id: str
    slice_no: int
    input: str
    answer: str
    citations: tuple[dict, ...]
    criteria: tuple[str, ...]
    plan: str = ""
    evidence: dict | None = None


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

    引用条目**给溯源 + 切块标识 + 切块正文**(#64 A1 推翻本函数原先「不给原文」的口径):
    原口径假设「看编号与出处就能判引用是否对得上题目」,但引用按**文档**合并编号、doc 级标题只取
    首块——同编号下其余切块的内容无从得知,judge 面对无法核验的论断只能记 0。给正文才可判;
    token 由两级上限罩住(见 ``_citation_lines``),超限显式标注。

    判分对象二选一:``plan`` 非空 = 规划切片面(评的是整份切片计划,产出段换成【切片计划】);
    否则 = 既有「文本产出 + 引用条目」形状(票 #59/#60 的线原样不动)。

    ``evidence`` 非 None 时追加【查证证据】段(#67 工作台线 / #69 任务线):商品/订单等
    **系统查询结果**原先不在材料里,判据②③对商品类结论**结构性不可核验**(judge 被要求
    「只依据给定材料」+「宁可判失败」,对真有据的库存/价格结论也只能记 0——2026-09-17 实评
    两条 0 的成因)。段落形状按载荷分派,见 ``_evidence_section``。
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
                "【该产出的引用条目(编号 → 出处 + 切块正文 / 系统查询记录)】",
            ]
        )
        lines.extend(_citation_lines(request.citations))
        lines.append("")
        if request.evidence is not None:
            lines.extend(_evidence_section(request.evidence))
            lines.append("")
    lines.extend(["【评分标准】"])
    lines.extend(f"{index}. {criterion}" for index, criterion in enumerate(request.criteria, start=1))
    lines.extend(
        [
            "",
            "【判定要求】",
            "- 每条标准独立判定,不受其他标准结果影响;宁可判失败,不要放过没把握的。",
            "- 只依据上面给的材料,不引入你自己的外部知识。",
            "- 动作执行结果(工单登记 / 草稿创建 / 审批提交)**不是「查证证据」**,"
            "不得因产出未给出其溯源而判失败(#64:那是真实动作不是编造,工具结果不进本材料)。",
            "- **商品/订单是系统查询结果**(【查证证据】段),不是语料切块:该类结论只要该段材料支持,"
            "即算有据,**不因其未标引用编号而判失败**;若已标编号,用【引用条目】里的记录条目核对"
            "(标记与证据须对得上)。反过来,该段里没有的商品/订单事实即凭空论断,照判不通过(#67)。",
            "- **同一文档可能并存不同口径**(正文叙述一处、附表 / 摘要行一处,数字可差一个取整幅度):"
            "产出所述数字与材料中该文档**任一处**记载相符(含「约」一类的约数)即算有据,"
            "不得因材料里另一处口径不同而判无据——但材料里一处都对不上的数字,仍按凭空论断判(#71)。",
            "",
            "【输出格式】每条标准输出一行,形如:",
            "<标准序号>|<0 或 1>|<一句话理由>",
            "0 = 未通过,1 = 通过。共 " + str(len(request.criteria)) + " 行,序号自 1 起、与评分标准一一对应;",
            "不要输出任何其他内容。",
        ]
    )
    return "\n".join(lines)


def _citation_lines(citations: tuple[dict, ...]) -> list[str]:
    """引用载荷 → 提示词里的「编号 → 出处 + 切块正文」行(空载荷如实标注,不假装有依据)。

    **正文必须给**(#64 A1,推翻本函数原先「只给溯源与切块标识」的口径):引用按**文档**合并编号,
    doc 级 ``title`` 只取首个被引切块的标题——同编号下其余切块写了什么,judge 从标题与编号上
    根本看不出来。2026-09-16 批次批的实录是:5 条判失败(客服线 4 + 选品 1)在载荷里**都有原文**
    逐字对应,judge 却因无从核验而记 0(它被要求「只依据给出的材料」+「宁可判失败」,没有第三条路)。
    给全文才让判据「结论性陈述有查证证据支撑」**可判**。

    token 有界靠两级上限,截断一律**显式标注**(不静默截、不静默丢):单块超 ``_CHUNK_EXCERPT`` 截到该
    上限并标全文长度;引用块累计超 ``_CITATIONS_BUDGET`` 后,其余切块只给标识 + 从略标注——
    judge 读到「从略」才知道自己看到的不是全部。
    """
    if not citations:
        return ["(无——该产出没有引用条目)"]
    lines: list[str] = []
    budget = _CITATIONS_BUDGET
    for entry in citations:
        title = entry.get("title") or "(无标题)"
        source = entry.get("source") or "(无来源)"
        lines.append(f"[{entry.get('number')}] {title} — {source}")
        record = entry.get("record")
        if record is not None:
            # 系统记录条目(#67;商品/订单):与切块行同形,但标识前缀即「这不是语料切块」
            lines.append(_record_line(record))
            continue
        for chunk in entry.get("chunks") or []:
            line, cost = _chunk_line(chunk, budget)
            lines.append(line)
            budget -= cost
    return lines


def _record_line(record: dict) -> str:
    """系统记录条目(商品/订单)→ 材料行:标识 + 查询结果正文(截断显式标注,同切块口径)。"""
    return f"    {record.get('id', '')}:{_excerpt(str(record.get('content') or ''))}"


def _evidence_section(evidence: dict) -> list[str]:
    """证据块 → 判分材料段(标题 + 正文,#69 起两种线两种形状)。

    标题随形状分派、不共用一句:工作台线(#67)是三桶 + 未被引的 FAQ 命中,任务线(#69)是
    系统记录类**逐调用**的查回结果——给一句话错了形状,judge 会去找材料里不存在的东西。
    """
    if "lookups" in evidence:
        return ["【查证证据(系统记录查询结果:订单/商品)】", *_lookup_lines(evidence["lookups"] or [])]
    return [
        "【查证证据(系统查询结果:商品/订单;FAQ 命中含未被引用的)】",
        *_evidence_lines(evidence),
    ]


def _lookup_lines(lookups: list) -> list[str]:
    """任务线证据块 → 材料行(#69):逐调用给「工具 + 参数 + 查回结果」,列表型结果逐行给。

    任务线切片的结论不标记录编号(编号式是工作台线 #67② 的能力),故这里给的是**查回的原值**:
    judge 照判定要求里那条口径核「该段材料支持即算有据」。行数超限的结果已在出块时裁剪
    (``agents.base.evidence_block_from``),裁剪事实随条目带 ``truncated`` / ``rows_total``,
    此处如实标注——不静默截(同 #64 A1 的口径)。
    """
    lines: list[str] = []
    for entry in lookups:
        params = json.dumps(entry.get("params") or {}, ensure_ascii=False, default=str)
        lines.append(f"工具 {entry.get('tool', '')} 查证(参数 {_excerpt(params)}):")
        result = entry.get("result")
        rows = result if isinstance(result, list) else [result]
        lines.extend(f"    {_excerpt(json.dumps(row, ensure_ascii=False, default=str))}" for row in rows)
        if not rows:
            lines.append("    (无记录)")
        if entry.get("truncated"):
            lines.append(
                f"    (该查询共 {entry.get('rows_total')} 行,本材料给出其中 {len(rows)} 行:"
                "产出提及的记录优先,其余取头部)"
            )
    return lines


def _evidence_lines(evidence: dict) -> list[str]:
    """查证证据块 → 判分材料行(#67):三类证据**全量**如实呈现,缺席与截断都标注。

    给全量而非只给被引的那部分:判据要判的是「结论有没有材料支撑」——只给被引证据,
    未被引的真凭实据就核不着,judge 只能对没把握的记 0(这正是 #67 两条 0 的成因)。

    正文逐条按 ``_CHUNK_EXCERPT`` 截断;不再设总预算——证据块按构造有界(FAQ top-3、
    商品 MENTION_MATCH_LIMIT 截断),与引用块的开放长度不是一回事。
    商品行的字段与用户面弹层(``core/drafting._product_source``)同源同义,分处两层的理由是
    受众不同:弹层要一行紧凑话术,这里要判据②要核的全部在售事实(价格/状态/库存)。
    """
    lines: list[str] = []
    faq_hits = evidence.get("faq_hits") or []
    if faq_hits:
        lines.append(f"FAQ 命中 {len(faq_hits)} 条:")
        lines.extend(
            f"    {hit.get('id', '')}:{_excerpt(str((hit.get('payload') or {}).get('content') or ''))}"
            for hit in faq_hits
        )
    else:
        lines.append("FAQ 命中:无")
    order = evidence.get("order")
    if order:
        product = order.get("product") or {}
        detail = (
            f"订单 #{order.get('id')} · 状态 {order.get('status')}"
            f" · 金额 {order.get('total_amount')} {order.get('currency')}"
        )
        if product:
            detail += f" · 商品 {product.get('sku')} {product.get('title')}"
        lines.append(f"订单查证:{detail}")
    elif evidence.get("order_id") is not None:
        lines.append(f"订单查证:订单 #{evidence['order_id']} 未查到(如实标注,未编造)")
    else:
        lines.append("订单查证:买家消息未提供订单号")
    products = evidence.get("products") or []
    if products:
        lines.append(f"商品查证(按买家消息文本查库)命中 {len(products)} 款:")
        lines.extend(
            f"    id={product.get('id')} SKU={product.get('sku')} {product.get('title')}"
            f" · 价格 {product.get('price')} {product.get('currency')}"
            f" · 状态 {product.get('status')} · 库存 {product.get('stock')}"
            for product in products
        )
        if evidence.get("products_truncated"):
            lines.append("    (命中已截断:以上不是全部——更多商品未进入本材料)")
    else:
        lines.append("商品查证:无命中")
    return lines


def _excerpt(text: str) -> str:
    """证据正文的单条截断(显式标注,同切块行口径)。"""
    text = text.strip()
    if len(text) > _CHUNK_EXCERPT:
        return f"{text[:_CHUNK_EXCERPT]}…(截断,全文 {len(text)} 字)"
    return text


def _chunk_line(chunk: dict, budget: int) -> tuple[str, int]:
    """一条切块 → (提示词行, 占用的预算长度);正文缺席与预算耗尽都如实标注。

    计费按**整行**收(标识前缀、正文、截断标注全算),不比正文长度——费与所见必须一致,
    少算前缀就会让实际提示词超出预算(评审指出的一处)。
    """
    chunk_id = str(chunk.get("id", ""))
    text = str(chunk.get("content") or "").strip()
    if not text:
        return f"    {chunk_id}:(无正文)", 0
    if budget <= 0:
        return f"    {chunk_id}:(正文从略——引用块已达总预算)", 0
    line = f"    {chunk_id}:{_excerpt(text)}"
    return line, len(line)


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
