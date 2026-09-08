# 增量 4 spec:业务子图 + 真实工具挂接 + 审批中心

## Problem Statement

作为卖家团队,当前系统只能规划与挂起审批,业务 Agent 全是占位桩:任何任务都不会产生真实效果(商品不会上架、订单不会流转、报告不会生成);审批批次里只有切片意图描述,审批人看不清「到底要对哪个商品做什么」;前端没有审批中心,审批人收不到实时通知,也无法一站式决定。增量 4 要让三个业务 Agent 真正干活、高危动作携带真实参数快照进审批护栏、审批人通过实时审批中心完成人工环节。

## Solution

三个业务 Agent 建成真实 LangGraph 子图挂接 `execute_slice`(方案 B:切片内收集、边界打包、批准后统一执行):子图 ReAct 循环内免审工具直行、审批类工具不执行只登记参数快照(如实告知 LLM 已登记);切片完成后按动作类型打包审批批次、切片边界 interrupt(一次携带切片全部批次)、审批通过后事务内统一执行(apply)已批动作。客服子图建成结构化两节点图(verify → draft),图级边约束钉死查证优先。新增 WS 实时通道(approval.requested/decided + 任务生命周期)与前端三视图壳 + 完整审批中心。B18 并发护栏(apply 时快照比对 + 行锁事务)与 B14 Langfuse trace 层级归属一并落地。

## User Stories

1. 作为卖家,我让 Manager 分析某品类市场,选品 Agent 真实查询趋势/竞品情报(Milvus)、评分并生成报告,随后自动创建商品草稿(draft.create,免审直行)
2. 作为卖家,我要上架商品时,审批批次携带真实参数快照(商品 ID/标题/SKU/目标状态),我能看清要上架什么
3. 作为卖家,我批准后商品状态才真实变更(效果后置执行);拒绝后 Manager 携拒绝原因重规划(跳过/替代/终止)
4. 作为卖家,同一订单的商品上架与改价按类型分装两个批次,不跨类型混批;同类型多动作同批同进同退,一次决定
5. 作为审批人,我在审批中心一次看到切片内全部待批批次(含任务上下文),逐批决定一次提交
6. 作为审批人,新审批到达时 WS 实时推送,无需手动刷新
7. 作为审批人,审批决定(决定人/时间/备注)全量落库,可审计追溯(A12)
8. 作为卖家,演练环境(影子模式)高危动作只记录不执行,审批中心显示影子段并可一键补执行;生产环境锁定真实审批(B15/A15)
9. 作为卖家,任务生命周期(挂起/完成/失败)在审批中心实时可见
10. 作为卖家,库存检查读真实 stock 字段并给出五档告警文案,不再依赖 LLM 自报库存(A7)
11. 作为卖家,订单状态流转(如发货)真实落库,非法流转被 7 态状态机拒绝,流转前经审批护栏(A6)
12. 作为卖家,订单列表/按状态查询可用;订单取消同样进审批
13. 作为客服,买家消息必须先查 FAQ/订单(verify 节点强制),才能进入草稿环节;图级边约束拒绝未查证直达草稿(B12)
14. 作为客服,多语翻译、FAQ 检索、订单查询、模板管理、情感分析、升级工单落表全部可用(A10/A11)
15. 作为卖家,两个任务并发操作同一商品时,护栏(事务行锁 + 快照比对)保证无脏写,冲突如实上报(B18)
16. 作为卖家,业务 Agent 超过 10 步强制终止,产出「未完成+原因」(B17)
17. 作为开发者,未授权工具对 LLM 不可见、禁做工具不存在,三层分类动态生效(B6/B7)
18. 作为运营,Manager 规划/子图执行/工具调用/审批决定在 Langfuse 全链路可追踪,审批决定与 trace 互链(B14)
19. 作为卖家,免审动作(草稿创建/编辑)直行不产生审批噪音
20. 作为卖家,服务重启后挂起任务从断点恢复,不重放已完成段(B5 与真实批次语义兼容)

## Implementation Decisions

### 业务子图与工具

- 新建 `agents/` 包:三个 Agent 各一个子图;选品与订单共用共享 ReAct 循环构造器(agent 节点 + tool 节点),客服手绘结构化两节点图
- ReAct 循环:LLM 选工具 → 执行 → 观察 → 再推理,上限 10 步,超限「未完成+原因」;工具清单由节点授权动态组装(ToolRegistry)
- 工具原子化:一个工具 = 一个动作,动作标识即工具名;旧复合工具(product_crud/order_workflow)按动作拆开
- 工具清单:
  - 选品:trend_query、competitor_analysis(读 Milvus market_intel)、scoring、generate_report(LLM)、draft.create(免审,A4 工具化)
  - 订单:list_orders、check_inventory(读库五档告警)、detect_anomalies、list_approvals、draft.create/draft.edit(免审)、product.publish/unpublish/update_price/delete、order.transition/order.cancel(审批)
  - 客服:verify 节点 = faq_search(Milvus)/order_lookup;draft 节点 = translate、生成草稿、模板管理、sentiment_analysis(可选)、escalate_ticket
- 工具执行器协议:auto 直行;approval 不执行、登记参数快照、向 LLM 返回诚实观察「已登记待人工批准」;forbidden 不出现在任何节点授权集(分类表未知动作默认 forbidden)

### 批次打包与 apply(方案 B)

- 切片执行完毕 → 按 action_type 分组 → 每组建批次(create_batch,真实参数快照,人类可读)→ 切片边界 interrupt(载荷携带本切片全部批次)→ 用户逐批决定一次提交
- 批准后 apply 阶段:事务内执行已批动作,先比对参数快照与当前 DB 值(漂移 → 该批整批不执行、如实上报,同进同退);行级锁(SELECT FOR UPDATE)串行化并发任务对同一行的操作(B18)
- apply 幂等:已生效动作跳过(防 durable 重放重复执行);批次落 executed 态
- 影子模式:shadow 批次只记录不执行不阻塞;审批中心显示影子段,补执行入口调 apply
- 拒绝/终止语义与增量 3 一致(拒后回流重规划 REPLAN_LIMIT=2,terminate 直接结束)

### 监督图改造

- execute_slice 内以编译子图替换 stub runner(子图状态经返回值回传,批次的持久化仍靠 approval_batches 表——挂起跨重启恢复靠批次表 + 监督图 checkpointer,子图不另挂 checkpointer)
- approval_points 保留为规划层提示(前端时间线断点展示),不再驱动打包
- 新增 apply 节点/步骤与冲突上报路径(如实告知用户)

### WS 实时通道

- python-socketio 挂 FastAPI;事件:approval.requested(批次数组)、approval.decided、task.created/interrupted/completed/failed
- 发射器以协议注入图节点与 REST 层(测试用内存发射器),图节点保持纯函数风格

### Langfuse 层级(B14)

- thread_id 为根 trace;Manager 规划、每切片子图执行、工具调用、审批创建/决定为层级 observation;LLM generation 挂入所属 trace(现有 LlmTracer 扩展 trace 上下文)
- 审批决定记录与任务 trace 互链(落 AgentTask 审计源)

### 前端:三视图壳 + 审批中心

- frontend/ 重建为新壳:三视图路由(驾驶舱/起草台占位,审批中心完整);审批中心 = 待批列表(REST)+ WS 实时更新 + 逐批决定一次提交 + 任务上下文/后续计划预览 + 影子段(仅 dev)+ 补执行按钮
- 契约 events.ts 扩展:ApprovalRequestedPayload 改为批次数组形状、新增 task 生命周期与 decided 事件形状、审批中心相关类型;契约测试对照断言

### 数据库

- 无新表/迁移:approval_batches 现有字段(actions 参数快照、status 含 executed/shadow)够用

## Testing Decisions

- 好测试标准:只测外部行为(子图结构/批次形状/DB 效果/事件时序),不测内部实现细节;不依赖真实 LLM(FakeLlm 脚本化工具调用),真 PG 用于事务/并发断言
- 接缝(沿用增量 3 conftest 风格,新测试直接 import 共享夹具):
  1. 子图结构测试(B6):编译产物断言节点工具可见集、边约束拒绝 verify→draft 非法迁移、10 步上限
  2. 执行器/分类单测(B7):auto 直行、approval 收集快照、forbidden 不可见
  3. 监督图集成:切片跑通 → 收集 → 同类型同批/不跨类型混批 → interrupt 载荷含真实参数 → approve → apply 落库 → 下一段;拒绝 → 回流
  4. apply 事务集成(真 PG):快照漂移冲突、并发两任务同商品无脏写、apply 幂等重放
  5. WS e2e:socketio 测试客户端断言事件时序与载荷
  6. 契约测试:events.ts ↔ 后端形状
  7. Langfuse:no-op tracer 断言层级调用(不依赖真 SDK)
- 先例:tests/test_tool_exposure.py(B6 已有先行)、test_supervisor_graph.py、test_approval_*.py、test_contract.py

## Out of Scope

- order.create 与库存扣减(B9)、CSV 导入(B8)、汇率快照(B10)——增量 5
- 起草工作台 UI(B11)、多语言起草 UI 呈现(B19)、会话记忆(B16)——增量 5
- 事件总线语义重设计(A4 已工具化,事件层触发图留增量 5)
- 驾驶舱/起草台视图完整实现——增量 5(本增量只立壳)
- 自然消息决定意图的 LLM 解析(增量 3 关键词规则已定)
- 旧前端冻结组件迁移——前端整体替换,无迁移

## Further Notes

- 存量提醒:REPLAN_LIMIT=2 与 terminate 已实现;批次 create/decide 幂等语义沿用
- 审批中心 REST 数据源沿用 GET /api/threads/{thread_id}/approvals;resume 端点的 {batch_id: decision} 多批一次决定形状沿用
