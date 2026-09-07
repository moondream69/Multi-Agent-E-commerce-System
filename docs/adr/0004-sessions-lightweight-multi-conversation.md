---
status: accepted
---

# 会话机制:轻量多会话(UI + 历史隔离)

## 背景

issue #5:无会话机制,不能新建/切换会话,历史持续累积。人工体验(2026-09-06)提出,2026-09-07 分诊 grill 定稿。

## 决策

- **轻量范围**:仅会话 UI 与历史隔离;LLM 不携带会话历史(ReAct 每任务独立保持不变)。会话记忆/摘要留给未来(`summary` 列闲置已久,暗示过该方向)。
- **表结构**:`conversations` 一表多行,`(customerId, sessionId)` 唯一;messages 仍为 JSONB 数组。现有单行迁移为 `sessionId="default"`(默认会话)。
- **会话生命周期**:每次登录默认开新会话;F5 刷新恢复到当前会话(localStorage 记当前 sessionId,复用 #4 铃铛的持久化模式);空白会话惰性落库——首条消息才建行并生成标题。
- **标题**:自动取自首条用户消息,截断 20 字。
- **删除**:硬删除(DELETE + 前端确认弹窗);删除当前会话后回到空白新会话。
- **UI**:ChatPanel 顶部工具栏(新建按钮 + 会话下拉),驾驶舱与客服工作台共享组件自动一致。

## 考虑的选项

- **重量版(LLM 携带会话历史)**:prompt 组装 + 窗口截断 + token 成本,范围大,另立 issue 单独设计。
- **拆两表(conversation_messages 逐条消息)**:规范化的收益在内部工具 + 演示规模下不成立。
- **软删除**:无审计需求,徒增一列与查询过滤。
- **每次刷新也新开**:与"进行中的对话"体验冲突,采用"登录新开、刷新恢复"。

## 后果

- REST 契约变化:`GET /api/conversations` 语义从"返回整行"改为"会话元数据列表";新增 `GET /api/conversations/{sessionId}/messages` 与 `DELETE /api/conversations/{sessionId}`;契约测试同步。
- Alembic 迁移:conversations 表加 `sessionId`、`title` 列。
- WS 入站 `chat:message` 增加可选 `sessionId` 字段(缺省回落默认会话)。
