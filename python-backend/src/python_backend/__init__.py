"""多 Agent 电商系统后端(重构目标态,ADR-0005)。"""

import asyncio
import sys

# psycopg 异步模式不支持 Windows 默认的 ProactorEventLoop,须在事件循环创建前切换为 Selector。
# 任何消费者(main/测试/脚本)都会先 import 本包,策略因此在首个循环创建前生效;Linux/Docker 走默认分支。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
