---
status: accepted
---

# 客服 Workflow 图级强制

客服 Agent 可绕过 sentiment→faq→template 工作流直接写回复:唯一约束是 SYSTEM_PROMPT 的「## 工作流程」纯文本,ReAct 图把「LLM 不调工具直接输出」定义为合法终态(`route_agent` 只看本轮有无 tool_calls,`tool_choice="auto"`)。决定:在共用 ReAct 图(`build_react_graph`)上增加**可选的 workflow 声明**机制——Agent 声明按阶段推进的图级约束,客服声明两阶段(阶段1 必调 sentiment_analysis + faq_search,顺序不限;阶段2 解锁全部工具、可自由回答),未声明 workflow 的 Agent 行为完全不变。动机为学习 LangGraph 图级约束(条件边/状态推进)兼顾客服回复确定性。

## 关键决策点

- **约束落在图上而非提示词**:图在阶段未完成时裁剪传给 LLM 的工具白名单(阶段外工具不可见),绕过时注入点名缺失工具的提示并强制回 agent 轮;不失败、不终止,轮次耗尽走既有 exhausted 兜底。曾试按阶段设 `tool_choice="required"` 提升确定性,但 DeepSeek(thinking 模式)拒绝该参数(400 "Thinking mode does not support this tool_choice"),已回退为仅靠循环强制拉回。
- **manage_template 不强制**:原工作流中它是条件性步骤("如果找到FAQ就用"),FAQ 无结果时模板必填会迫使 LLM 调用注定失败的工具;保持条件性,交 LLM 判断。
- **提示词配合不改 SYSTEM_PROMPT**:图在首轮注入阶段提示、阶段切换时注入一次解锁提示,与图强制天然同步,无两份真源漂移问题。

## 考虑的选项

- **纯提示词强化(SYSTEM_PROMPT 强调必调工具)**:成本最低,但 LLM 仍可忽略,不解决根因;曾为唯一约束且已失效。
- **事后校验/重试包装器(execute_task 外层检查 steps 后重试)**:复杂度高于图内循环(需额外状态机、重试管理),且绕开 LangGraph 的状态推进能力。
- **严格三步顺序强制(sentiment→faq→template 依次必调)**:状态机最严格,但与业务语义冲突(FAQ 无结果时模板必填进退两难),放弃。

## 后果

- 客服绕过场景会多耗轮次与 token(无 `tool_choice="required"` 捷径,靠循环拉回);代价换取图级确定性与 LangGraph 条件边/状态推进的学习收益。
- 通用性与客服提示词有轻微耦合:注入文案在 graph.py 内、按阶段通用,工具名由 `phase.required_tools` 动态生成,不硬编码,复用面保留。
- 未声明 workflow 的选品/订单 Agent 零行为变化,有回归测试锚定(`test_workflow_none_behavior_unchanged`、`test_react_graph.py` 5 个既有测试零断言改动)。
- `build_react_graph` 的 `tools` 参数放宽为 `Sequence[ToolProtocol]`(协变,type-checker 建议),运行时无差异。
