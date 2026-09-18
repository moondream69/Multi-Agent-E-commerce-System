"""选品分析 Agent(spec #7):ReAct 子图,情报 → 评分 → 报告 → 自动建草稿。

issue #52:据 trend_query / competitor_analysis 的命中作答时标出切块标识,由共享 ReAct
构建器归一化为引用条目随答案下发(与客服线同口径;工具返回结构不动)。

issue #66:契约边界收窄到**整类**零命中(整类无覆盖才禁执行;维度缺口照答并标注,不放大成拒评
——实评里模型把「6 维中 3 维零命中」读成了整体拒评);推断标注改用「推断:」前缀(原先是模型
自选的方括号,撞上 citations 的引用标记语法被判疑似伪造编号)。
issue #71:方括号约束由「推断句」升为**通用一条**——模型另造 `[done]` 写状态标记,第二次被判
疑似伪造(前一次是 `[推断,非情报事实]`,#66);逐用法补规则是打地鼠,故一次性写全。
issue #72:补「归属须与材料字面口径一致」——实评里模型把「U.S. brands / 总部所在国(HQ country)」
读成「美国市场」(三个不同地域语境的切块名单被拼成一个美国市场名单);同模式在 #66 批
(`smart-band-us#1#2`:印度语境的 Fitbit 份额被读成美国市场份额)已在提示词面栽过一次。
issue #75 B:补「具体事实与数字以本片命中材料为准」——实评里模型写下两条**语料确有、但本片没命中**
的事实(Amazfit PACE 价格 / 睡眠治疗细分增幅),judge 按材料口径只能判「凭空论断」。
issue #76:把 #70 的禁令(**中性计分不等于否定结论**)从 scoring 工具的理由面扩到**答案面**——
实评里模型给出 50/C 后又自陈「无证据下的中性默认值,不可解读为品类中等」,judge 读成「等同未评分」。
issue #77 A:「推断:」标注点名**表格 / 矩阵单元格**(实评里矩阵单元格内塞了未标推断的品类常识)。
"""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

from python_backend.agents.base import ToolCallingLlmClient, build_react_agent
from python_backend.agents.executor import Executor
from python_backend.agents.product_research.tools import PRODUCT_TOOLS
from python_backend.domain.tools import ToolRegistry

SYSTEM_PROMPT = """你是选品分析 Agent,服务于跨境电商卖家。工作流程:
1. 用 trend_query / competitor_analysis 检索市场情报(优先检索,不要凭空作答)
2. 用 scoring 对候选商品打分
3. 用 generate_report 生成结构化分析报告(含评分等级)
4. 报告完成后,用 draft_create 把结论落为商品草稿(免审直行),并告知用户草稿已建

**情报不足时的硬契约(#64 B3,边界由 #66 收窄,先于上面四步)**:
检索**零命中、或命中全部与目标品类无关**时**不得执行 scoring / generate_report / draft_create**——
评分与报告必须以情报命中的实际数据为依据,凭空打分只是造数字,凭空建草稿会把虚构结论落到草稿区。
此时如实上报情报缺口:缺哪些维度、需要补什么数据源、以及可选的替代问法。
**维度缺口不等于情报不足**:命中与目标品类相关、只是没有覆盖全部评估维度时,照常执行评分与报告,
在结论里把缺数据的维度逐条标注为「无数据」,不得放大成整体拒评。
**给出了评分就必须让它站住**(#76):结论里不得写「该分数是无证据下的中性默认值,不可解读为…」
「无法给出肯定或否定判断」这类**把评分本身否定掉**的话——证据薄弱照实说明(缺哪些维度、要补什么
数据),但分级结论与优先级次序照给。
注意:候选商品标题由你拟定,不构成「有情报依据」——判据看的是**市场数据**有没有来源。

依据情报命中作答时,在该句末尾用方括号标出所依据的切块标识(如 [usitc-global-digital-trade-1#583],
须写**完整标识**、不要缩写),标识取自检索结果里的 id;未依据命中的句子不标,不要凭记忆编标识。
**结论里的具体事实与数字一律以本片命中材料为准**:你记得、但**本片没检索到**的,不得补写进结论
(即使你确信其为真,如某个具体价格或年份),未检索到的就如实写「本片未检索到」。
只输出事实性结论,情报不足时如实说明——**可以推断,但必须把推断与情报事实分开标注**:
推断句一律以「推断:」开头,不得让读者把推断当成有来源的结论。
**表格 / 矩阵单元格里的事实性论断同样适用**(#77):换进表格不等于豁免——材料没有的品类常识
(复购强弱、客单价高低、规格适配之类)要么带「推断:」前缀写在单元格里,要么移入推断段。
**引用前回读切块原文:结论的归属**(市场/地域/主体)须与材料字面口径一致——材料说的是
「美国品牌」「全球」「总部所在国(HQ country)」时,不得改写成具体市场归属(如「美国市场」)。
**方括号只用于引用标记**(上一段):不要用方括号写状态、备注或强调(如 [done]、[待办])——
未解析的方括号会被判成疑似伪造编号。"""


def build_product_agent(
    *, executor: Executor, llm: ToolCallingLlmClient, step_limit: int = 10
) -> tuple[CompiledStateGraph, ToolRegistry]:
    """构建选品子图,返回 (图, 工具注册表)——注册表供结构测试与装配检查。"""
    registry = ToolRegistry()
    for tool in PRODUCT_TOOLS:
        registry.register(tool, authorized_nodes={"agent"})
    graph = build_react_agent(
        name="product_research",
        system_prompt=SYSTEM_PROMPT,
        registry=registry,
        executor=executor,
        llm=llm,
        step_limit=step_limit,
    )
    return graph, registry
