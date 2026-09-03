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

**事件**(Event):
Agent 间松耦合通知,经事件总线发布,供页面实时展示。当前为进程内总线。十类:报告生成、产品创建/更新、订单状态变更、回复生成、升级触发、任务分配/完成/失败、Agent 状态变更。

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
