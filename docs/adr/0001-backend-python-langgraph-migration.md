---
status: accepted
---

# 后端全量迁移到 Python(FastAPI + LangGraph)

后端原为 NestJS(TypeScript)单服务:3 个 Agent + 手写 ReAct 循环、pgvector 检索、进程内事件总线。决定:在迁移分支上以 Python 单服务(FastAPI + 手绘 LangGraph StateGraph + SQLAlchemy/Alembic + python-socketio)全量替换,前端与 API 契约保持不变;迁移性质为**学习练习**,不以上线为目标(2026-09-04 已转正,见文末)。

## 考虑的选项

- **留在 NestJS 迭代**:否——动机是学习 Python 生态,留在原地不满足目标。
- **仅迁移 Agent 编排层,保留 NestJS 外壳**:否——双运行时、跨服务通信成本大于收益;当前架构小而紧耦合,拆开得不偿失。
- **只做 POC 不迁移**:考虑过;直接以全量迁移为练习场(分支内进行,main 不受影响),一次到位。

## 后果

- 契约等价仅由 pytest 契约测试保证:测试对照 `frontend/src/types/events.ts` 断言 `agent:event` / `chat:response` 形状,不靠人肉同步。
- 已知业务问题(Agent 不总按 workflow、模板无持久化)不属于修复范围;两个例外:seed 改为幂等、Embedding 失败由静默零向量改为显式报错。
- 若将来决定上线替换,需另立决策(并发模型、日志观测、安全加固),本 ADR 不覆盖。

## 转正(2026-09-04)

迁移验收通过(python-backend 36 pytest 全绿含 WS e2e 真链路、契约测试对照 `frontend/src/types/events.ts`),决定:Python 版为唯一后端,NestJS 版 `src/` 与 `test/` 已删除(git 历史可查)。上线替换仍需另立决策,不因转正自动获得上线资格。
