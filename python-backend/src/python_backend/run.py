"""开发/生产统一运行入口。

Windows 下 psycopg 异步模式不支持默认的 ProactorEventLoop,而 uvicorn CLI 会在加载应用前创建
事件循环(包入口的策略设置来不及生效),故统一从这里启动:先切 SelectorEventLoop 再交给 uvicorn。
Linux/Docker 下该策略调用为安全 no-op 分支。
"""

import asyncio
import sys

import uvicorn

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if __name__ == "__main__":
    # loop="none":uvicorn 在 Windows 上硬编码 ProactorEventLoop(loops/asyncio.py,无视事件循环策略),
    # 而 psycopg 异步模式不支持 Proactor——禁掉 uvicorn 的循环管理,由 asyncio.run 按上面设置的策略建 Selector 循环。
    uvicorn.run("python_backend.main:app", host="0.0.0.0", port=3000, workers=1, loop="none")
