"""多 Agent 电商系统后端(重构目标态,ADR-0005)。"""

import asyncio
import sys

# psycopg 异步模式不支持 Windows 默认的 ProactorEventLoop,须在事件循环创建前切换为 Selector。
# 任何消费者(main/测试/脚本)都会先 import 本包,策略因此在首个循环创建前生效;pytest-asyncio
# 等第三方消费者由此兜底(正式入口 run.py 已改用 loop_factory)。策略 API 自 3.14 起弃用、
# 3.16 移除,本包锁 3.13,故暂以 ty: ignore 保留;升级 3.14+ 时按各 asyncio.run 站点传 loop_factory 迁移。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # ty: ignore[deprecated]
