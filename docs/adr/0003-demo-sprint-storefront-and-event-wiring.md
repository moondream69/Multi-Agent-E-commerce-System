---
status: accepted
---

# 演示冲刺:买家前台旅程与跨 Agent 事件接线

定位为「作品集 / Demo」项目后,1 个月冲刺要把三 Agent 从「各自独立演示」推进到「完整电商闭环」:前端加买家前台(商品商店 → 下单 → 订单 → 售后),后端把 6 类已声明却从未 emit 的业务事件全部接线(工具 emit → Agent 订阅 → 实际动作),并补齐主动通知、审计持久化与 LLM 事件循环阻塞修复。明确排除:部署、认证、全面异步化、agent_memory、购物车、登录。

## 关键决策点

- **混合下单模式**:买家页面「立即购买」走 REST 直下 pending 订单(演示买家「张伟」按 email 定位,无账号体系);订单状态流转仍由订单 Agent 在聊天中指挥完成。REST 直下保证演示观感(真实电商页面),Agent 流转保留叙事(多智能体戏份)。
- **事件接线方案**:工具构造注入可选 `EventBus`(默认 None,既有无参构造测试零改动),emit 前判空。6 类业务事件全部接线:选品报告 → 订单 Agent 用 LLM(json_mode)提炼商品 → `product_crud` 创建草稿(提炼失败降级为报告标题 + 默认价,仅日志);订单状态 / 库存告警 → 客服 Agent 生成中文通知文案(状态映射表,不占 LLM 调用)→ emit `customer.notification`。
- **主动通知走新 WS 事件 `chat:notification`**:与 `chat:response` 三形状并存、互不干扰;同时保留 `agent:event` 全量桥接(后台视角事件流),双显为有意设计(后台 / 买家两个视角)。
- **客服扩两个工具**:`order_lookup`(售后查订单真实状态)+ `escalate_ticket`(emit `escalation.triggered`);SYSTEM_PROMPT 同步更新,工作流强制(阶段1 必调 sentiment+faq)不变。
- **审计与历史持久化**:`agent_tasks`(任务开始 in_progress / 结束 completed|failed + output,写失败仅日志)、`conversations`(聊天消息 JSONB 单行追加)接入既有的表——两张表建了从未用,消除「表建了不用」质疑;`agent_memory` 明确不做(无跨任务记忆需求,演示价值低)。
- **LLM 阻塞小修**:ReAct 图 agent_node 与客服 LLM 工具、embedding 检索工具的同步调用统一 `asyncio.to_thread` 卸载出事件循环(单次 LLM 调用最长 60s,此前会冻结整个服务);DB 同步操作保持现状并在本 ADR 记录为已知权衡。
- **契约加法式**:`frontend/src/types/events.ts` 只加键不改键(新增 2 事件类型、payload 接口、`chat:notification` 信封、store REST 类型),契约测试同步增补。

## 考虑的选项

- **事件全部不接线、演示人肉串联**:零成本但与「多智能体协作」定位矛盾,弃。
- **仅接线 1–2 点(报告→草稿)**:范围小,但「主动通知」是演示最亮眼的故事点,放弃收缩。
- **前端加真实角色系统/登录**:Demo 定位下无收益,排除。
- **全面异步化(DB 也改 async)**:超出小修范围,后移。

## 后果

- 事件墙:演示中事件流从 4 类生命周期事件扩到 12 类全量,「事件驱动」成为可见的架构故事。
- 客服 Agent 工具 4 → 6,现有测试构造点需补两个参数(零逻辑变更)。
- 通知双显可能造成事件流重复观感,已在文档与演示脚本中说明(后台/买家视角)。
- LLM 自动草稿依赖 json_mode 提炼质量,失败路径有降级兜底,不阻断闭环。
