---
status: accepted
---

# Agent 评测线(最小评测面):金标场景集与 LLM-as-judge

## 背景

2026-09-13 完成度审计的定向核查发现:**仓库内无任何 Agent 评测机制与需求记录**(40 个 issue 全状态、全仓代码/文档、旧系统 main、52 份 handoff 均零命中);「evals」仅作为 Langfuse 选型理由出现(ADR-0005、CONTEXT.md)。该需求在「访谈→宪章」之间流失,由 issue #45 定格为**最小评测面**——不追赶访谈原貌、不扩展版图。

当时无法立即开闸的两项前置,现已就绪:

1. **语料地基**(ADR-0007 已交付):`faq` 100 / `market_intel` 2288 切块非空,引用小点随答案下发——「报告/草稿是否基于证据作答」这条判据**可证伪**;
2. **观测地基**:自托管 Langfuse 在栈内,SDK(4.15.1)具备 dataset / score / experiment 全接口;任务 trace_id 由 thread_id 确定性派生,分数可无查库地挂回任务 trace。当前仅 trace 在用,score/dataset 零调用;部署观测因 `.env` 双密钥留空整体 no-op。

ADR-0007「评测对接」段对本线提出三项义务:LLM-as-judge 评「引用是否支撑答案」;**机械防伪引**作附加判据(judge 对"引用了不存在的编号"不可靠,机械校验近零成本);评测跑批记录语料批次标识,历史分数可归因到语料版本。

**业务三域版图不变**(纯质量面);评测运行时不阻塞任务链路。

## 决策

### 金标场景集

- **真源 = 仓库 YAML**(`docs/evals/*.yaml` 一类),与语料线同构:**真源在仓、可 diff、可冻结;Langfuse datasets 只是它的投影**。输入是固定文本(选品指令 / 买家消息),**LLM 不参与生成评测输入**。
- 三类金标:**选品报告 / 客服草稿 / 规划切片**,每场景携带**判据清单(rubric)**;按 tracer 顺序铺开——选品报告先行(2–3 条),模板立住后扩另两类。
- 「客服草稿」**两条线都评**:起草工作台(同步调用、无任务轨迹)与任务内客服 Agent 线(经 `POST /api/tasks`,取切片产出)。

### 跑批器

- **离线 CLI,子命令 `run` / `score` 解耦**:run 驱动真实任务、落**产出快照**(本地 JSON)并投影 Langfuse(dataset run + 任务 trace);score 从已存产出回评并写分数。**换 rubric、换 judge 只烧 judge token,不重跑任务**——一次真跑选品 2–3 分钟并烧真 token,重跑是评测迭代中最贵的动作。
- 评测**黑盒走 REST**(评的是产品面产出,非图内部);规划切片不另设「只规划」捷径,复用同任务跑批的 plan 段。
- 跑批环境 = **专用净库、每次跑批前重建**,与演示素材物理隔离;**不重灌语料**——检索链路纯读 Milvus payload(实测:PG 两投影表在生产读路径零引用),省去 20 分钟重灌且不影响判据。
- 金标场景一律设计为**免审直行**;跑批遇 interrupt 视为场景设计缺陷,**显式报错、不自动批准**(不把机器决定混进评测语义)。
- 串行执行;若使用 Langfuse experiment 机制,其并发参数压至 1–2(默认 50 与仓内 LLM 并发闸=2 相性差)。

### 判据

- **LLM-as-judge**:经中转站的 `claude-opus-5`(跨厂,避免与被评对象同厂自评偏差;官方模型页 2026-09-14 实测核实);**Anthropic 原生 Messages API**(其中转站对 `claude-opus-5` 只开原生格式,用户实测告知)——官方 `anthropic` SDK、`base_url` 指向中转站。judge 只发朴素字段(messages + max_tokens),风格与输出约束全部写进提示词;effort / thinking 等原生参数可用但**不作正确性依赖**(中转透传程度不一)。
- 标度:**每判据 0/1(通过/失败)+ comment 收 judge 理由**——LLM judge 在细标度上噪声大。
- **机械防伪引**(零 LLM):答案引用标记与 citations 载荷一一对应 + **解析不到的残留标记 = 疑似伪造**;校验纯函数落在**引用解析唯一解析点**(`core/citations.py`),避免第二套解析规则漂移。机械判据独立于 judge、全量跑,不设开关。

### 落库与版本锚

- 评测结果**只落 Langfuse**(scores + dataset run + trace 互链;分数可带 trace_id / dataset_run_id / metadata);**不建 PG 评测表**——为未证实的「离线报表/门禁」需求建表违最小面,是后续增量决策。
- 语料版本锚:**内容哈希指纹为主锚**(由语料真源离线重算,确定性、不依赖任何库)+ **batch_id 尽力附记**(从摄入所指库读「覆盖整库」的最近批次;读不到留空如实标注)。理由:`batch_id` 只活在 PG 台账且无「当前批次」语义(部分摄入批次会冒充最新),指纹不受净库重建影响。
- Langfuse 启用(OPERATIONS 生产切换清单第 6 步)成为评测线的**前置人工步骤**;缺密钥时跑批器**显式报错**,不静默降级。

### 边界

- 不改业务三域版图、不新增产品视图、不阻塞任务链路;评测是运营/质量面,不是产品面。
- 配置面新增 `judge_*` 组(judge_model / judge_api_url / judge_api_key);不动 `llm_*`。

## 考虑的选项

- **金标集载体**:只 Langfuse datasets / 只仓库 YAML / **YAML 真源 + 投影** —— 选第三:与语料线同哲学(YAML 可 diff 可冻结),datasets 供 UI 对比、可按批次重灌,真源不搬家。
- **跑批形态**:pytest integration 标记 / REST 端点 / **离线 CLI** —— 选 CLI:烧真 token、要真服务、受并发闸约束的跑批不进测试套件语境;端点则把评测变成产品面。
- **评分时机**:**解耦 run / score** / 一体化 run_experiment —— 选解耦:rubric 首版几乎必然要改,重评只烧 judge token。
- **落库面**:+PG 评测表 / 只 PG / **只落 Langfuse** —— 选只 Langfuse:最小面;无「评测看板」诉求时不建表。
- **judge 选型**:同厂换档 / 国产第二家 / 本地 Ollama / **跨厂中转站 Claude** —— 选中转站 `claude-opus-5`:跨厂消除自评偏差,直连可付,零代理零海外计费面。
- **草稿评测面**:工作台线 / 任务内线 / **两条都评** —— 选都评:工作台品草稿质量,任务内线品「查证→引用」全链行为。
- **版本锚**:只 batch_id / 只指纹 / **指纹为主 + batch_id 附记** —— 选第三:净库重建后 batch_id 运行时不可得,指纹离线可算且不受库影响。
- **跑批库**:dev 库跑后清理 / **专用净库每次重建** —— 选净库:清理脚本是新的失误面,忘了跑就污染演示素材。

## 后果

- **新增**:`judge_*` 配置组;评测包(场景加载 / 机械校验 / judge 客户端 / 编排)与评测 CLI;`docs/evals/*.yaml` 真源;`core/citations.py` 机械校验纯函数;依赖新增 **`anthropic` SDK**(判分客户端走原生 Messages API)。
- **验收基线新增 B29**(评测跑批:快照落盘、分数与 trace 互链、机械防伪引可判、分数带语料版本指纹)。
- **不进快速套件**:评测 CLI 与真实跑批走净库(与摄入 CLI 同待遇);核心逻辑离线替身测。
- **成本与节流**:一次真跑选品 ≈2–3 分钟;三类金标按 tracer 顺序铺开;judge 调用量小、成本可忽略。
- **边界风险**:中转对原生参数(effort / thinking)的透传程度不一——judge 只依赖 messages 输入输出、不依赖参数生效;若中转站虚标/降智,换站或换型号属配置面切换、不动结构。
- 评测与能力解耦:评测面不追赶实现进度(ADR-0007 既定);#45 由本线 spec 承接后关闭。

## 修订记录

| 日期 | 条目 | 旧文 | 新文 | 来源 |
|---|---|---|---|---|
| 2026-09-14 | 新建 | — | 金标场景集(YAML 真源 + Langfuse 投影)、跑批器(run/score 解耦 CLI + 专用净库)、判据(中转站 claude-opus-5 judge + 机械防伪引,0/1 + comment)、落库(只 Langfuse;内容哈希版本锚为主 + batch_id 附记);边界(黑盒 REST、不改三域、不阻塞任务链路) | #45 定格需求;评测线四轮访谈;官方模型页与 langfuse SDK 实测核实 |
| 2026-09-14 | judge 协议 | OpenAI 兼容协议(与 `LlmService` 同形状) | **Anthropic 原生 Messages API**(官方 `anthropic` SDK,`base_url` 指中转站)——用户实测告知:其中转站对 `claude-opus-5` 只开原生格式 | 用户实测;claude-api 官方文档核实 |
| 2026-09-16 | judge 可达性(实测,决策未改) | 中转站对原生 Messages API 放行 | 该站已改策略:**裸 Messages 调用一律 403**「This API endpoint is only accessible via the official Claude CLI」⇒ 现行 judge 调用面(官方 SDK + base_url 指中转站)被拒。**本 ADR 的决策不变**(judge 端点/型号仍是配置面切换,不动结构);实测记录见 `docs/handoffs/evidence-2026-09-16-issue61/`,恢复/换站待维护者裁决 | 票 #61 跑批实测(2026-09-16 08:47) |
