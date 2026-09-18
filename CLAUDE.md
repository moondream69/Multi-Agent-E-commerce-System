# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 当前状态:重建已合并 main,生产剖面运行中

旧系统冻结在 `880b62d`(重建起点)。**`rebuild` 已于 2026-09-18 快进合并入 main(`12fabb4`)并推送**;同日执行**生产切换**(`.env` 的 `ENVIRONMENT=prod`,六项验证全绿,证据 `docs/handoffs/evidence-2026-09-18-cutover/`)——**真实业务数据尚未导入**(数据到手后走 `docs/OPERATIONS.md`「试运行数据 provisioning」全流程);**要回演练剖面把 `.env` 的 `ENVIRONMENT` 改回 `dev` 后 `docker compose up -d app`**。README.md 为目标态文档;开发命令、约定与排坑以本文件 + CONTEXT.md 为准。业务决策唯一约束 = @docs/adr/0005-architecture-rebuild-production-charter.md;验收基线 = @docs/acceptance-scenarios.md;术语表(目标态,以它为准)= @CONTEXT.md。旧系统术语/类名只在被取代的决策记录中保留,不得当作现行架构。

## 开发命令

```bash
# 基础设施
docker compose up -d                                          # 启动 9 服务:Postgres/Redis/app 之外,Milvus/etcd/MinIO/ClickHouse/Langfuse×2 属默认栈(模拟流量 --profile sim、Ollama --profile embed 按需;首次需 docker compose build)

# 后端 (python-backend/,Python 版为唯一后端)
cd python-backend
uv run python -m python_backend.run                # 启动(端口 3000;Windows 下经 run.py 切 SelectorEventLoop——uvicorn 直接跑 main 会因 psycopg 不支持 Proactor 而启动失败)
uv run pytest                                      # 全部测试(e2e/integration 需真实服务在线,离线秒 skip)
uv run pytest -m "not e2e and not integration"     # CI 同款快速套件
uv run alembic upgrade head                        # 数据库迁移(14 张表:11 业务 + 3 语料;checkpoint 表由 PostgresSaver 自建,不在 Alembic 内)
uv run ruff check .                                # Lint (无 --fix,自动修复用 `ruff check . --fix`)
uv run ruff format .                               # 格式化
uv run ty check .                                  # 类型检查 (Alembic 迁移已排除)

# 前端:生产形态已随 app 镜像同源托管——浏览器直接开 http://<主机>:3000(前端 + API 同端口)
cd frontend && npm run dev                           # 开发态 Vite (5173,/api 与 /socket.io 代理到 3000;要 HMR 才用)
cd frontend && npm run lint / lint:fix               # ESLint 检查/自动修复 (前端,无 --fix 不改写)
cd frontend && npm run format / format:check         # Prettier 格式化/只检查
cd frontend && npm test                              # vitest 组件测试(新增/修改组件;test:watch 监听)
cd frontend && npm run build                         # 前端构建 (tsc + vite)

# 模拟流量 (需后端已启动;容器内为 docker compose --profile sim up -d)
cd python-backend && uv run python -m python_backend.simulator --once             # 冒烟一轮
cd python-backend && uv run python -m python_backend.simulator --loop 300         # 每 300 秒一轮

# 试运行合成数据(确定性 seed=20260913,500/200/2000;导入顺序·批量激活·回填 SQL 见 docs/OPERATIONS.md)
cd python-backend && uv run python scripts/gen_synth_data.py

# 语料摄入 CLI(离线;真源 = docs/corpus/*.yaml,Milvus 两集合 + PG 两表只是它的投影——ADR-0007)
cd python-backend && uv run python scripts/ingest_corpus.py freeze --pdf <url|本地路径> --doc-id <标识> --title "…" --source "…" --published-at YYYY-MM-DD --category <情报四类之一>
cd python-backend && uv run python scripts/ingest_corpus.py ingest      # 幂等(同 id 覆盖);全量重灌 ≈20 分钟(本机 Ollama ≈0.5 秒/块)
```

> ⚠️ `npm run lint` 只检查、不自动改写——需要自动修复时用 `npm run lint:fix`。
> lint/format 已移入前端:所有 npm 命令须在 `frontend/` 下执行(仓库根已无 package.json)。
> Python 侧规范工具为 ruff(lint+format)与 ty(type check),配置在 `python-backend/pyproject.toml`。
> uv 在 PATH(`E:\Python\Scripts\uv.exe`)。PyPI 直连不畅时:`HTTPS_PROXY=http://127.0.0.1:7897 uv sync`。
> ⚠️ 改后端**或前端**代码后须 `docker compose build app && docker compose up -d app`——镜像 COPY 源码 + 构建期打包前端产物(`Dockerfile` 多阶段),无挂载,不重建即跑旧码。生产形态前端与 API 同源单端口(`create_app(static_dir=...)` 注入位挂载);跨 origin 访问须列入 `CORS_ORIGINS`,否则 socket.io 握手 400。
> ⚠️ `alembic check` 只看有无 `modify_type` 判漂移(`checkpoint_*` 与 `uq_orders_reference_partial` 恒报 remove 类噪声),勿整体非零即慌。
> ⚠️ ty 有平台差异:Windows 专属分支(`if sys.platform == "win32":`)里的 `# ty: ignore` 在 Linux 目标下会被判"未使用"而致 CI 红。推送前用 `uv run ty check --python-platform linux .` 复现 CI。
> CI(`.github/workflows/ci.yml`)在 push(main/rebuild)与 PR 上跑:后端 ruff/ty/快速 pytest,前端 lint/vitest/build。
> ⚠️ 快速套件(`-m "not e2e and not integration"`)须保持**离线可跑**(CI 无任何外部服务):新增依赖 PG/Milvus 的用例请标 `integration` + `requires_postgres` 守卫(⚠️ 该守卫按「连得上即跑」:`DATABASE_URL` 不带 `:5433` 死端口覆盖直接 `uv run pytest` 会**直写 .env 指向的真库**——2026-09-13 两度残留清理,样板见 evidence/cleanup*.sql,引用 users 的表须先删);离线自检命令与背景见 issue #13(⚠️ **派发可能跑测试的 sub-agent 时,提示词里必须写死这条覆盖命令**——评审/探索 agent 不会自己带,2026-09-14 #52 轮两度直写 dev 库,处置样板见 `docs/handoffs/evidence-2026-09-14-issue52/`)。当前基线(死端口仿真)**536 passed / 67 skipped / 54 deselected**(净额 597;在线全量(净库)449 passed / 0 skipped;前端 vitest 74 passed;含 #39 起草台商品查证 2 例 PG 门控、#42 类目直查再 1 例门控,故 skip 计 67)(增量为 #13 任务行缝、增量 8 通知缝、#21 会话/买家/工单/报表四缝 + 生产装配接线守卫、#20 会话消息流、#25 摘要字符串口径、#26 POST /api/tasks 响应键驼峰收口、#27 思考模式 reasoning_content 回传、#28 sim 客户端超时、#29 fx 基址配置、协作管线三事件与空正文上抛护栏(思考预算)、#34 商品/订单只读缝 + 订单列表端点 + 汇率卡片数据面 + 审批 plans 旁挂、#35 product_lookup(注入替身 5 例;另有 2 例 PG 实查离线时计 skip,故 62→64)、#36 前端静态托管(create_app 的 static_dir 注入位 + dist 解析 10 例)、#37 影子段剖面过滤 + 补执行端点闸(create_app 的 shadow_mode 注入位 4 例)、#38 客服 product_lookup 查证(1 例)、#40 跨剖面恢复按批次持久化 mode 分派(3 例:approve/reject/混合防御)、#41 决定人 decided_by(端点 3 例 + PG B4 加断言;写法 = 登录用户名)、#42 类目查询(product_lookup category 离线 1 例 + PG 门控 1 例;数据台类目筛选 1 例计前端套件)、#43 五语离线证据(7 例:五语参数化 + 矩阵守卫 + 非法语种拒绝)、#44 选品串联(2 例:competitor_analysis 功能 + 四步走通)、#47 前端渲染缝(vitest 6 例)、#48/#49 语料摄入与铺开(`test_corpus_{chunking,pipeline,files}.py` 19 例 + PG 门控)、#50 统一检索与工具暴露面、#51 引用解析(`test_citations.py` 8 例 + 契约守卫)、#52 引用横切(`test_subgraphs.py` 2 例)、#54 语料清尾(离线 3 例 + PG 门控 1 例)、#56 评测线骨架(`test_eval_{scenarios,files,judge}.py` 场景加载/真源文件/配置闸)、#57 机械防伪引(`test_citations.py` 加伪造场景用例 + `check_citations` 纯函数)、#58 评测 run 子命令(`test_eval_{snapshot,corpus_anchor,projection,runner}.py` + `test_app_wiring.py` 装配守卫:tracer 接线缺陷即此轮修)、#59 评测 score 子命令(`test_eval_judge_client.py` 解析与请求形状 16 例 / `test_eval_scoring.py` 编排、派生与型号锚 14 例 / `test_eval_scores.py` 落分与读回 10 例 / `test_eval_scenarios.py` 加重复判据守卫 1 例)、#60 客服草稿两线评测面(`test_eval_runner.py` 工作台分支 5 例:快照/分派/起草失败/空草稿/建 trace 失败 + `test_eval_projection.py` 评测根 trace 1 例 + `test_eval_scenarios.py` locale 字段 4 例 + `test_eval_scoring.py` 工作台快照照评 1 例;真源 `docs/evals/customer-draft.yaml` 5 条)、#61 规划切片评测面(`test_eval_runner.py` 规划场景 2 例:任务线随带 plan 段/真源落盘 + `test_eval_scoring.py` 规划分支 3 例:整份计划判/键 `#plan#`/缺 plan 报错 + `test_eval_snapshot.py` plan 读写与坏形状 4 例 + `test_eval_judge_client.py` 提示词【切片计划】段 1 例;真源 `docs/evals/planning-slices.yaml` 3 条=选品/订单/客服三域)、#63 评测落分 id 五分量 + 读回核对键(`test_eval_scores.py` id 材料断言改 + 旧行顶包守卫 1 例;同 id 写入是**就地更新且 trace 不动**,详见 #63)、#64 评测驱动的质量收口(判分材料补**被引切块正文** + 动作类陈述边界句——只给 doc 标题与切块编号时判据「引用是否支撑答案」不可判,现批 5 条失败即由此误判;空正文护栏 = **同预算重试一次 → 记未完成 → 任务 failed**;切片上下文随 Send 下发**原请求**(Send 是完整替换状态,执行段原先只拿到切片描述);检索零命中不得执行 scoring/generate_report/draft_create;快照升 **3** 落 `incomplete` 且 `executed` 如实 + 跑批器空产出闸;场景重铸 `coffee-maker-us` → `smart-home-us`(原品类在情报语料零覆盖);**agent 输出预算放宽到 16384**(`AGENT_MAX_TOKENS`,三个调用点共用——空正文的根因是思考吃穿了默认 2000))**、#65 起草线预算与空正文重试(起草线 `max_tokens` 2000 → 16384,**与 agent 线同源常量** `AGENT_MAX_TOKENS`;空正文 → 同预算原样重试一次,仍空才上抛 → 端点 500(「宁可不给也不给空草稿」的姿态不变);新类型 `LlmEmptyContent` 区分「可补救的空正文」与别的 LlmFailure——后者传输层已按 1s/2s 重试过;新增 `test_drafting_blank.py` 3 例,全替身进离线套件——`test_drafting.py` 全模块 PG 门控,故按 `test_drafting_locales.py` 同法另立模块)、#66 评测三处收口(①选品契约边界收窄到**整类**零命中 + **维度缺口照答**并逐条标注「无数据」(实评里模型把「6 维中 3 维零命中」读成了整体拒评);②推断标注约定为「推断:」前缀 + 要求写**完整**切块标识——机械判定维持宽规则不动(一切未解析方括号 token 皆疑似伪造);③`outdoor-trend` 重铸为 `remote-health-us`(情报语料「Remote Healthcare Monitoring」段有覆盖)+ 覆盖守卫改为**逐条**核对锚文本并核到**语料文件本身**(旧守卫只钉一条 note 文案);判据①补「等价的分级结论」口径(「无法分析/未评分」不算);实评 `run-20260917T091150Z` 6 场景 21 条 **18 过 / 3 败**——四条目标失败全翻正,仍败三条见证据档分诊)、#67 工作台线判分材料与商品证据引用(①**快照升 4** 落 `evidence` 查证证据块 + judge 材料渲染【查证证据】段(商品/订单事实全量给,**未被引的 FAQ 命中也给**)+ 判定要求补口径句「商品/订单系系统查询结果、不因缺编号判失败、材料里没有的照判凭空」;②**三类证据统一编号**(ref 贯穿 FAQ/订单/商品,`core/drafting._numbered_sources` 是唯一编序点)+ **引用载荷扩系统记录条目**(`build_citations` 的引用源分两类,记录条目给 `record` 不给 `chunks`,语料条目显式落 `kind: "corpus"`;机械线「与命中集相交」式对记录条目跳过;前端 `Citation` 拆语料/记录联合类型、弹层按 `kind` 分流);测试 `test_drafting_records.py` 4 例 + `test_citations.py` 6 例 + `test_eval_judge_client.py` 4 例 + `test_eval_{snapshot,scoring,runner,contract}.py` 各补)、#68 预算收口五处兄弟调用点(`AGENT_MAX_TOKENS` **移到 `infrastructure/llm.py`**——executor 与 agents/base 互相 import,常量放中间层才不成环;`_execute_{translate,scoring,sentiment_analysis,generate_draft}` + `core/memory` 摘要四处 2000/800 → 16384,**不分档**(思考长度不是正文长度的函数);空正文重试提炼为 `llm.complete_with_blank_retry`(#65 起草线的护栏升为全仓调用点共用的一份),`test_llm_service.py` 3 例 + `test_executor.py` 下限抬档并纳入 generate_draft)、#69 任务线系统记录证据块(票面「客服 verify 线」只是半张——实核失败片 `agent=order_management`,订单线**没有** `order_lookup`,它靠 `list_orders` 读全库;`agents/base.py` 的 `evidence_calls_from` / `evidence_block_from` 收 `order_lookup`/`product_lookup`/`list_orders` 三类**查回结果**(收口面写死 ⇒ 选品/规划线物料零变化),`AgentState` 与 `CustomerState` 各加 `evidence_calls`(客服线原 `evidence` 键改名 `verify_calls`——调用记录 ≠ 查回的值),`make_agent_runner` 出块并**在此裁剪**(列表型超限按「产出提及优先 + 头部补齐 + 总行数标注」,实测 2000 行),`core/graph.py` 的 `run_output`(durable 重放)与 `merged` 双双落块,**`evals/snapshot.slices_from_results` 补读 `evidence`(原漏,新用例抓出)**;judge 增 `_evidence_section`(标题按形状分派)+ `_lookup_lines`(工作台三桶分支不动);`test_evidence_block.py` 9 例 + `test_eval_judge_client.py` 2 例 + `test_eval_runner.py` 1 例 + `test_executor.py` 2 例)、#70 评分理由与「无数据」自述相抵(`_execute_scoring` 理由只能用给定材料 + **定性事实也算材料**;`_execute_generate_report` 同口径 + **中性计分不等于否定分级结论**——窄跑 `run-20260917T153600Z` 8 条判据①被「无区分度/不构成判断」类自我免责声明抵掉 ⇒ 补这两句,二跑 `run-20260917T160511Z` 3 场景 **9/9** 全过)、#71 判分材料保真与方括号通用约束(①**单块上限 600 → 1400 + 引用块总预算 6000 → 12000**(`evals/judge.py`):旧值是 #64 A1 的拍脑袋数,实测**截断 363/608 个被引切块**——`smart-band-us#1#2` 判 0 的真机制是支撑句「$8.4 billion」在切块 `#860` 的**第 678 字**被截在材料外(票面断言「答案依据的正文口径确实在材料里」经 dump 核实**不成立**),judge 只见同文档附表行的 8,000 ⇒ 判「与命中不符」;1400 > 语料切块 max(实测 1302)、12000 > 实测单切片被引正文最大(10877)⇒ 本批五场景材料重 dump **0 截断 0 从略**;②判定要求补「同一文档多口径(正文/附表),与任一处记载相符即算有据」;③方括号约束由「推断句」(#66)升为**通用一条**,三处带引用的提示词各加一句(选品 `product_research`/客服 `DRAFT_SYSTEM`/起草线)——`[done]` 类状态标记是同类问题**第二次**被判疑似伪造(前一次 `[推断,非情报事实]`);测试 `test_eval_judge_client.py` +3(保真回归/口径句/预算断言随档)+ `test_subgraphs.py` +2 + `test_drafting_locales.py` 加断言;**复核批 `run-20260917T171844Z`**(全量 11 场景 48 条 **46 过 / 2 败**:材料 dump 0 截断 0 从略——本批 76 个被引切块里旧上限 600 会截 44 个、新上限 0 截断;机械线 15 切片 0 失败、`[done]` 类零复发;旧批三败同键全翻正但**答案文本不同 ⇒ 非受控归因**;两败经零 LLM 分诊均为**模型侧越界**——海关草稿「查询渠道」段写库内无据的流程断言 / 可穿戴把「U.S. brands・Table 7.1 的 HQ country」读成「美国市场」,立票 #72;任务 token ≈2.12 元))、#72 无据说明收口与归属口径(①**发生位置锁定在 verify 线**:复核批 `cs-task-customs-zh` 的 verify 按 manager 切片描述点名(「…海关官网、物流承运商…」是描述里的常识、非证据)把整份答复写完,draft 只见成品回「【草稿已完成】」⇒ 答案落成 149 字**元对话**、判据**全空过**(判据「禁止式+条件式」写法对无实质内容自然空转——评测线对元对话无防线);②收口四处——客服 `VERIFY_SYSTEM` **只输出查证整理**(逐条列切块标识与支持事实,不写答复正文/不补库内空缺)+ `DRAFT_SYSTEM` 两条(无据流程/渠道说明不得写成确定口吻 + 必须输出完整草稿正文)+ 工作台线 `core/drafting.py` 同口径 + 选品 `SYSTEM_PROMPT`「引用前回读切块,归属须与材料字面口径一致」(`U.S. brands`/`HQ country` ≠ 美国市场;同模式 #66 批已栽过一次);③复核批 `run-20260917T184044Z` 50 条 **49 过 / 1 败**(唯一败 = **引用标注不全**:枚举原文在 `#859`、模型检索到了却没标引用,而判分材料只渲染**被引**切块 ⇒ judge 按「材料无记载」判凭空;处置 = 不处置记录);窄批复验 `run-20260917T200510Z` 8 场景 29 条 **29/29**(verify 输出转成「## 查证整理」纯证据表;customs 答案主动写「未查到相关说明…不便凭一般经验给您确定答案」且 `#1#3` 翻 1;选品三场景均主动声明「全球口径、非美国市场专项数据」;回评因 judge 单行漏分值栏中止一次,`resilient-score.py` 补跑——补跑器须传 run 内**全部**场景文件)、#73 元对话防线(评测线对**元对话产出**无防线——`cs-task-customs-zh` 149 字「【草稿已完成】…」在**禁止式/条件式**判据下三条**逐条空过判 1**:判据族缺「适用性地板」,禁止式防「说了不该说的」,防不住「什么都没说」;`evals/judge.py` 判定要求补「**产出形态先于内容**」——元对话 ⇒ 每一条判据一律判 0、条件式判据前提落空**不等于**不适用,豁免面限定为「**面向对象**的动作结果交代」(旧判词正是把元对话读成「属动作执行结果」而放行 ⇒ 「只声明自己完成动作」显式判回元对话);**受控复评**(旧快照单条、`--run` 指单快照目录 + 新 run 名 ⇒ 同 trace 并列两行、**不改写旧行**)两轮均 **3/3 翻 1→0**;不做机械闸(机械判「元对话」只能靠模式串会误杀合法短答复,且跑批闸语义是「无可评对象才中止」)与不加生产护栏(诱因已由 #72 从提示词面消掉)的理由见 #73)、#74 订单线伪检索断言收口(`detect_anomalies` 描述改「检测**给定文本**…**不检索订单库、不定位订单**」+ order 线 SYSTEM_PROMPT「工具结果须按真实语义转述 / 不得作系统级负向断言」;`test_subgraphs.py` +2;全量批复核 `run-20260917T224753Z` 11 场景 56 条 **50 过 / 6 败**——#74 修复保持零复发,六败零 LLM 分诊:3 条判 0 正确(模型侧纪律)+ 2 条判分材料缺口(系统契约无载体 / 跨片事实不在本片引用池)+ 1 条判据①×多片规划形状,立票 #75-#77)、#75 判分材料【系统契约】段与命中池(①**系统契约段**——订单片如实陈述的状态流转/工具能力全是真系统契约(`VALID_TRANSITIONS` / `ORDER_TOOLS`),而材料四段无载体 ⇒ judge 按口径判「凭空」是正确执行(留档键再触发);新 `evals/system_contract.py` 由**代码派生**(状态机 + 本域工具清单含免审/审批),`JudgeRequest.contract` 非空即渲染【系统契约】段 + 判定要求补口径句(与契约一致即算有据、矛盾即判失败、不要求引用标记);**触发面收口在订单域**(选品/规划/工作台/客服线材料**逐字节不变**,材料 diff 12 片 0 差异为证);②**快照升 5 记 `hits`**(本片检索命中池的切块标识,去重保序、只落 id)——`citations` 是**被引池** ⇒ 「检索到了没标引用」与「凭记忆补写」原先在快照上同形(`smart-band-us#3#2` 分诊卡点);出块在 `agents/base.retrieval_ids` → `graph.py` 的 run_output/merged → `slices_from_results`;**先更正了票面前提**:切片 Send 只给「本片 + 原请求」(`graph.py:266`),「跨片复用」机制上不成立;③选品 SYSTEM_PROMPT 补「具体事实与数字以本片命中材料为准」;**窄批 `run-20260917T235505Z` 8 场景 33 条 33 过 / 0 败 / 5 不适用**,`cs-task-returns-zh#2#3` 判词「状态流转…**与系统契约一致**」= 直接验证)、#76 judge 第三值「不适用」与答案面口径(①**判据适用面**:判据对象在本片**结构上不存在**时 judge 判 **2 → 不落分**(与机械线「零对象判不适用、不作通过计」同口径),适用条件写死(「没把握/证据不足/无法核验」都不是不适用;**元对话不适用本例外**,#73 防线不许被放掉);`RubricScore.applicable` + `parse_judgment` 收 2 + `_record_scores` 跳过 + **`NotApplicable` 经 `on_skip` 浮上回显面**(不静默:机械线的不适用可从产出形状复算,judge 的不适用复算不出来);本批真实使用 5 次、零滥用,同时是**一处行为变更**(5 条条件式判据由上批「判 1」转为「不适用」——上批判词本就写「该条件性判据前提不成立」,当时只有 0/1 两词可用);②**答案面**把 #70 禁令(中性计分不等于否定结论)从 scoring 工具理由面移植到选品 SYSTEM_PROMPT)、#77 产出/规划纪律(①「推断:」标注点名**表格 / 矩阵单元格**(选品 SYSTEM_PROMPT);②`core/planning.py` 切片描述纪律——不得把用户没提供的信息写成既有前提,缺标识写成待补项(实测生效:同场景 `plan` 由「调取**该买家**跨境订单…」变为「**待用户补充订单号/下单账号**后核对…」)+ `agents/base.slice_prompt` 转述纪律一句(「【本切片职责】是系统给你的任务说明,不是用户原话」,一处三域共用);后两项行为面本批**未复现**,纪律保留待下批看复发率);**全量批收口 `run-20260918T003356Z`** 11 场景 35 条 **35 过 / 0 败 / 3 不适用**(全量批首次 0 败;上批六败按**同判据文本**全翻正;三处「未复现」纪律面本批**全部复现且合规**——smart-band-us 矩阵表后接「推断:…」/四场景 plan 描述逐条改写为「待用户补充订单号或买家 ID 后…」「不得自行假定」/选品分级结论 + 缺口如实标注(「中性默认…不可解读」类自我免责零命中);契约段判词级再验证:returns#3#3「均与系统契约一致」;judge 第三值 3 次真实使用零滥用;材料抽检 0 截断 0 从略;成本 ≈1.38 元;证据 `docs/handoffs/evidence-2026-09-18-fullbatch-closeout/`))**——`run`/`score` 是**离线 CLI**(烧真 token、要真服务),不进快速套件,核心逻辑一律替身或假件;`run` 按场景 surface 分派产出线(任务线 `/api/tasks`;工作台线 `/api/drafting` + 跑批器自建评测根 trace `eval:<场景id>`),两线快照/分数 schema 零分叉;`/api/products` 契约用例原离线 skip,现经替身常跑;**67 个运行时 skip 是既有 out-of-scope 面,勿顺手去动**)——摄入脚本是**离线 CLI**(需 Ollama/Milvus),不进快速套件,语料用例一律替身或门控;端点族触库操作一律经 `create_app` 注入位(`app.state.*_store`),新增替身沿用 `tests/conftest.py` 的 `InMemory*` 形状。

## 技术栈

FastAPI + LangGraph · PostgreSQL 16(向量在 Milvus,不入 PG;访问经 VectorRepository)· Redis 7 · DeepSeek v4 Flash (LLM) · 本地 BGE-M3 via Ollama (Embedding, 1024维) · python-socketio (WS 广播) · React + Vite + socket.io-client · uv

## 核心架构

```
用户输入 (REST /api/tasks) → Manager 规划(≤5 切片 + 依赖声明) → 监督图逐层 Send 扇出
                             → 业务子图(免审直行、审批动作收集参数快照) → 按类型打包批次
                             → 切片边界 interrupt → 批准后事务内 apply(批内同进同退) → 汇总
```

### 关键模块与职责 (python-backend/src/python_backend/;前端项与 scripts/ 另注路径)

| 模块 | 文件 | 职责 |
|-----|------|------|
| `ManagerPlanner` | `core/planning.py` | LLM 规划切片计划(≤5 步)+ 校验重试 + fallback 关键词路由;`AGENTS` 元组为业务域清单 |
| 监督图 | `core/graph.py` | manager → prepare → 逐层 Send 扇出 → execute_slice(子图挂接/批次打包/interrupt/apply)→ 拒后回流重规划(REPLAN_LIMIT=2) |
| 业务子图 | `agents/{product_research,order_management,customer_service}/` | ReAct 循环(10 步上限,B17);客服为结构化 verify→draft 两节点(图级查证优先,B12) |
| `ToolExecutor` | `agents/executor.py` | auto 直行 / approval 收集参数快照 / `apply_batch_actions`(事务+行锁+快照比对,漂移整批回滚,B18);构造注入位 = llm / vector / embedding / `ProductLookupStore`(#35 商品定位,SKU/标题/类目三分支——类目为 #42 补的 A5 缺口;`db/product_lookup.py`,生产默认 PG) |
| 审批批次 | `core/approvals.py` + `db/approval_store.py` | 三层风险分类、`ApprovalBatchStore` 协议(create/decide 幂等,重放安全)、PG 实现 |
| `VectorRepository` | `vector_repo/base.py` | 向量访问抽象(Milvus 实现,pgvector 可切换) |
| 事件与观测 | `core/events.py` / `infrastructure/tracing.py` | `EventEmitter`(WS 事件;协作轨迹三事件 task.planned / slice.started / slice.completed 由图内发射,B23)/ `TaskTracer`(Langfuse 层级,B14) |
| 通知组装与存储 | `core/notifications.py` / `db/notification_store.py` | 效果描述→通知载荷(状态映射表 7 文案 + 五档库存文案,零 LLM),`emit` = 组装→落库→广播;`notification.created` 由 apply/REST **提交后** emit;`NotificationStore` 按用户扇出写 + 回读(每组 50 条服务端截断,未读 = read_at 空),GET / POST read 端点为读路径(poke 提交后广播) |
| 端点族存储缝 | `db/{task,conversation,customer,ticket,report,product,order}_store.py` | 端点触库一律经 `create_app` 注入(`app.state.*_store`,默认 PG 实现,测试注入 `InMemory*` 替身——离线快速套件不触库,#13/#21/#34);`PostgresConversationStore` 挂起审批判定读注入的批次存储、`PostgresTicketStore` 买家名经注入的 `CustomerStore`(join 降级为读端点拼装);会话消息流读端点(`GET /api/conversations/{session_id}/messages`,#20)同经此注入位,替身 = `InMemorySessionMemory`;新增此类端点照此缝注入,勿在端点内直调模块函数 |
| 只读数据面 | `api/app.py` + `db/{product,order}_store.py` / `infrastructure/fx.py` | 数据台(`GET /api/products` 全量、`GET /api/orders?status=&limit=&offset=` 服务端筛选分页,**纯只读**——ADR-0006 边界,编辑诉求须另立 ADR;商品表前端筛选 = 关键词/状态/类目,#42)+ 汇率卡片(`GET /api/fx` = 当期汇率 + 缓存时刻 `fx:at:<币>` 伴生键 + 近 7 日订单快照走势按日取末笔);审批两端点信封带线程级 `plans` 旁挂(`task_store.get_task().slice_plan`,ADR-0005 任务上下文 + 后续计划预览);挂起路径(create_task / resume 的 interrupt 分支)即落 `slice_plan`,勿只写终态;影子段按剖面过滤——prod 隐藏且补执行 403(`create_app` 的 `shadow_mode` 注入位,默认关,#37/验收 B15) |
| `LlmService` | `infrastructure/llm.py` | `complete()` + `complete_with_tools()`(function calling);失败统一包装 `LlmFailure`(fallback 只承接它) |
| 语料摄入 | `corpus/` + `scripts/ingest_corpus.py`(python-backend/ 下) | `freeze`(PDF → 语料文件;真源可 diff)/ `ingest`(切块 → 嵌入 → Milvus upsert + PG 投影 + 批次台账 `corpus_batches`);切块标识 `<文档标识>#<序号>` 确定性派生 ⇒ 同 id 覆盖(**含清尾**:文档变短时删旧后段块,#54);**真源 = `docs/corpus/*.yaml`,库内两表两集合只是投影**——改语料改文件重灌,勿手改库;用法/取材口径/边界见 docs/OPERATIONS.md「语料供给」 |
| 引用解析 | `core/citations.py` | 引用小点唯一解析点:`build_citations(text, sources, *, allow_ordinals=False)` 归一化 + `retrieval_hits(tool_result)`(只认 `hits` 形状);**引用源两类**(#67):语料命中(切块级溯源,同文档合并编号)与系统记录(商品/订单查库结果,条目给 `record` 不给 `chunks`);解析不到的标记原样保留(机械防伪引);`allow_ordinals=True` **只给起草线**(`core/drafting.py`,序号式须显式放开,否则句中的 `[2]` 会被静默锚定、幻觉洗白);消费面 = 起草服务 / 客服 `draft_node` / ReAct 作答轮(选品线横切) |
| 切片证据块 | `agents/base.py` + `evals/judge.py` | 系统记录类查证(`order_lookup` / `product_lookup` / `list_orders`)的**查回结果**随切片产出落 `evidence`(#69;工作台线是起草端点载荷,#67):`resolve_tool_calls` 已拿到结果 → `tool_node`/`verify_tools` 另收一份 → `make_agent_runner` 出块(**裁剪在此**:列表型超限按「产出提及优先 + 头部补齐 + 总行数标注」)→ `_execute_slice` 落 `run_output`(durable 重放)与 `merged`(get_task 的 results)→ 快照 → judge `_evidence_section` 按形状分派(`_lookup_lines` 给任务线;工作台三桶分支不动);没有查证即 `None`——选品/规划线只有检索类工具 ⇒ **那些线的判分材料零变化** |
| 前端渲染缝 | `frontend/src/components/AgentMarkdown.tsx` | 全仓唯一 react-markdown 配置点(+ `remark-gfm`;原始 HTML 不透传,无 XSS 面);渲染面 = 会话消息流 / 起草台草稿 / 审批中心执行报告 / **驾驶舱切片答案**;**带 citations 载荷的三面**(起草台 / 执行报告 / 切片答案)`[n]` → 可点上标(点开六项溯源),会话消息流面不带引用载荷、`[n]` 保持字面文本;导出 `citationsOf` / `answerOf` 两个载荷读取器(调用面不止一处,读法收在这里);用户消息刻意保持纯文本 |

### Agent 模式

三个业务 Agent 是 LangGraph 子图,只声明身份与工具清单,由 LLM 决定调用顺序;**审批动作调用后不立即生效**——登记待人工批准,批准后事务内统一执行(效果后置):

```
选品  build_product_agent: trend_query / competitor_analysis / scoring / generate_report / draft_create(报告→自动草稿,A4)
订单  build_order_agent: 只读与 draft 编辑免审;上架/下架/改价/删除/订单流转/取消进审批(效果后置)
      定位 product_lookup(SKU/标题/类目→列表,免审直行,#35/#42;多候选须列候选澄清)——切片 Send 不传依赖产出,
      解析与操作须在同一 ReAct 循环内串联
客服  build_customer_agent: verify(faq_search / knowledge_search / order_lookup / product_lookup)→ draft 两节点,未查证不可达草稿(B12;商品查证 #38;统一检索 #50)
引用  横切能力(不属任何单域):ReAct 构建器(agents/base.py)的 tool_node 收集检索命中(retrieval_hits_from,客服 verify 线同用)
      → 作答轮 build_citations 归一化;有检索命中的答案随 results[切片号] 带 citations(#51 客服线 / #52 横切选品线);
      提示词各域自述引用要求(标切块标识、不凭记忆编标识);无命中的产出(评分/查单)不标
```

### 如何新增 Agent

1. `agents/<name>/tools.py`:定义 `ToolDefinition` 清单(OpenAI function 形状;工具名与动作标识经 `action_of` 显式映射)
2. `agents/<name>/agent.py`:`build_<name>_agent(executor, llm)` 返回 (编译子图, ToolRegistry);结构化图直接手绘节点
3. 挂接:`core/planning.py` 的 `AGENTS` 元组 + Manager 提示词领域路由;`main.py` 的 `build_agents()`
4. 新动作一处注册:`agents/executor.py` 中集中注册(REGISTRY.register:风险分类/中文标签/处理函数;`agents/registry.py` 仅定义类);前端标签经 GET /api/actions 渲染,不再硬编码。审批动作的 `apply` 若产生对外可见效果,**返回效果描述**(`core/notifications.py` 的 `EFFECT_*`),由调用方在事务提交后组装通知——不要在事务内 emit

## 环境配置

字段以 `python-backend/src/python_backend/settings.py` 为准(仓库根 `.env` 单一真源,compose 共读);下表列关键项:

| 变量 | 用途 |
|------|------|
| `database_url` | PostgreSQL 连接(SQLAlchemy URL,psycopg 直连经 `postgres_dsn` 属性去前缀) |
| `redis_host` / `redis_port` | Redis 连接 |
| `llm_api_key` / `llm_api_url` / `llm_model` | DeepSeek(OpenAI 兼容协议) |
| `llm_max_concurrency` | LLM 并发闸(默认 2,DeepSeek 账号级限流防护) |
| `embedding_api_url` / `embedding_model` / `embedding_dimension` | Ollama bge-m3(1024 维);无 OpenAI 降级,不可用显式报错 |
| `milvus_uri` | Milvus Standalone 端点 |
| `langfuse_host` / `langfuse_public_key` / `langfuse_secret_key` | 自托管观测,host 留空即禁用(no-op) |
| `environment` | 环境剖面:`dev`=演练(影子模式)/ `prod`=生产(审批锁死);影子模式由它派生,运行时不可切换 |
| `auth_jwt_secret` / `auth_token_ttl_hours` | JWT 签名密钥(生产必改:`openssl rand -hex 32`)/ 有效期 |
| `auth_admin_username` / `auth_admin_password` | 初始管理员凭据(启动时 lifespan 懒 seed;密码留空则跳过) |
| `approval_ttl_hours` | 审批批次存活时长(预留:当前无自动过期清扫,见 docs/OPERATIONS.md) |
| `cors_origins` | 允许的跨域来源列表 |
| `fx_api_url` | 汇率 API(基准 CNY;Redis 缓存 4h,下单快照落库) |

> ⚠️ Embedding 服务不可用时 `EmbeddingService` 显式报错(不静默降级为零向量)。
> 前端类型是 API 契约唯一真源:`frontend/src/types/events.ts`(对应契约测试 `python-backend/tests/test_contract.py`)。

## 数据库约定

- 枚举 status 列一律经 `db/models.py` 的 `_status_column_type()` 声明(`native_enum=False` + `values_callable`,落库 = 小写 value,与迁移/server_default/JSON 契约一致);**新增枚举列照抄,勿靠 `Mapped[X]` 推断**(推断出原生枚举 → 批插渲染 `::<名>status` 报错,issue #12);改口径 = 数据迁移;声明面由离线用例 `tests/test_enum_declarations.py` 遍历 `Base.metadata` 自动守卫(无需登记清单)
- 语料两表(`faq` / `market_intel`)与 Milvus 两集合是 `docs/corpus/*.yaml` 的投影:覆盖式更新走摄入 CLI(同 id 覆盖 + `corpus_batches` 批次台账,ADR-0007),**勿手改库内行**
- dev 库 = `mae`(测试直写,带 tag 行会累积);`multi_agent_ecommerce` 是旧系统冻结库,**勿动**

## Agent skills

### Issue tracker

Issue 跟踪在 GitHub Issues,用 `gh` CLI 读写。见 `docs/agents/issue-tracker.md`。

### Triage labels

五个规范角色直接作为标签名(`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`)。见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局:仓库根一个 `CONTEXT.md`,ADR 在 `docs/adr/`。见 `docs/agents/domain.md`。

### Session handoffs

每会话收尾写 `docs/handoffs/session-handoff-<date>-<topic>.md`(该目录 gitignore,不入库);开头写明"下会话主题"。
