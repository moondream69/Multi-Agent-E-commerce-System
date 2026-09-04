# Multi-Agent E-commerce System

一个多 Agent 电商管理系统:用户输入经意图解析、任务路由,由特定 Agent 通过 ReAct 循环(推理 ↔ 工具调用)完成选品分析、订单管理、客户服务三类任务。

## 运行实体

**Agent**:
承担一类任务的执行单元,由系统提示词 + 工具集定义。当前有三个:选品分析(ProductResearch)、订单管理(OrderManagement)、客户服务(CustomerService)。
_Avoid_: 机器人、助手、智能体机器人

**工具**(Tool):
供 Agent 声明、由 LLM 调用的最小可执行功能(如产品 CRUD、FAQ 检索、翻译、报告生成)。
_Avoid_: 能力、插件

**任务**(Task):
一次被路由到某 Agent 的输入请求,带类型与(可选)明确目标;状态流转:pending → in_progress → completed / failed。
_Avoid_: 请求(避免与 HTTP 请求混淆)

**任务类型**(TaskType):
三类——选品分析、订单管理、客户服务;决定路由到哪个 Agent。
_Avoid_: 意图、模块

**回复模板**(ReplyTemplate):
客服话术的标准文本,含变量占位(如 {order_id}),按 场景(scenario)+语言(locale) 唯一;持久化于 reply_templates 表,由客服 Agent 经 manage_template 工具查找/填充/新增。
_Avoid_: 话术库、快捷回复、标准回复

## 编排

**意图解析**(IntentParser):
将自然语言输入判定为任务类型的环节,当前为确定性关键词规则。
_Avoid_: 分类器(在 AI 论文里它是模型,这里是规则)

**编排器**(Orchestrator):
按任务类型(或显式目标)把任务路由到 Agent,并广播任务生命周期事件。
_Avoid_: 调度器、路由(后者通常指网络层)

**ReAct 循环**:
LLM 在推理与工具调用之间迭代,直到产出最终答案的闭环。已由 LangGraph 状态图等价实现。
_Avoid_: 聊天循环、推理链

**工作流**(Workflow):
Agent 可选声明的图级执行约束:按阶段推进,每阶段定义必调工具集、可见工具白名单、可否直接回答。当前仅客服声明两阶段(先必调 sentiment_analysis 与 faq_search,再解锁全部工具);未声明则行为不变。
_Avoid_: 流程编排、SOP、任务流

**事件**(Event):
Agent 间松耦合通知,经事件总线发布,供页面实时展示。当前为进程内总线。十二类:报告生成、产品创建/更新、订单状态变更、回复生成、升级触发、库存告警、客服主动通知、任务分配/完成/失败、Agent 状态变更。
_Avoid_: 消息(与聊天消息混淆)、回调

**演示买家**:
无账号体系下代表买家的固定客户(seed 客户「张伟」,按 email `zhangwei@example.com` 定位);商店页下单绑定到该客户,前台订单列表与客服通知都以他为视角。非多买家体系——只有这一个买家视图。
_Avoid_: 用户、账户、张伟(中文语境可直接说,术语表中可与「演示买家」并列)

**买家前台旅程**:
买家视角的页面闭环:商品商店 → 立即购买(REST 直下 pending 订单)→ 我的订单 → 客服中心(售后聊天)。前端视图导航(驾驶舱/商店/订单/客服)中的商店/订单/客服三视图构成前台,驾驶舱为管理员/后台视角。
_Avoid_: 门店、商城、用户旅程

**商品草稿**:
订单 Agent 收到选品报告(`report.generated`)后自动创建的商品(draft 状态,未上架)。由 LLM 从报告提炼字段(sku/title/price/category/description),提炼失败降级为报告标题 + 默认价。上架 = 商品状态流转为 active。
_Avoid_: 自动上架(草稿≠上架,主动作是更新状态)

**主动通知**:
客服 Agent 订阅订单状态变更 / 库存告警后,主动发给买家的通知(买家未说话,客服先来消息)。经 `customer.notification` 事件 → WS `chat:notification` 推送到聊天面板(淡黄系统气泡);同时保留 `agent:event` 全量桥接(后台视角)。
_Avoid_: 推送、弹窗、客服消息

**库存告警**:
库存检查工具在 `当前库存 / 安全线 < 1` 时发出的事件(五档:售罄/严重不足/偏低/接近安全线/充足,仅前四档告警),触发方为订单 Agent 的 check_inventory,消费方为客服 Agent(转发主动通知)。
_Avoid_: 补货提醒(那是建议动作,不是事件)

**后端服务**:
对外提供 REST 与 WebSocket 接口、承载整套编排的单一服务。当前为 FastAPI(Python)。
_Avoid_: 服务器、API 后端

**外部服务**:
LLM(DeepSeek,OpenAI 兼容协议)与 Embedding(Ollama bge-m3,1024 维)。
_Avoid_: AI 服务、模型服务

## 迁移决策(2026-09-04 已转正落地,保留作为术语背景)

**全量迁移**:
将后端整体替换为 Python 单服务,前端与 API 契约不变。
_Avoid_: 重写(暗示推倒重来)

**LangGraph 化**:
以 LangGraph 状态图实现原本手写的 ReAct 循环与 BaseAgent 状态机。
_Avoid_: 框架化(泛)

**迁移分支**:
在 git 分支内实施迁移,main 保持 NestJS 版本不动。
