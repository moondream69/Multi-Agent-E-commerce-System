"""开发/生产统一运行入口。

Windows 下 psycopg 异步模式不支持默认的 ProactorEventLoop,而 uvicorn 会自建事件循环
(loops/asyncio.py,无视事件循环策略),故统一从这里启动:显式以 SelectorEventLoop 作
loop_factory 交给 asyncio.run(不用已弃用的事件循环策略 API);Linux/Docker 走默认循环。
"""

import asyncio
import sys

import uvicorn

if __name__ == "__main__":
    # loop="none":禁掉 uvicorn 自身的循环管理,由下面 asyncio.run 的 loop_factory 建循环。
    server = uvicorn.Server(
        uvicorn.Config("python_backend.main:app", host="0.0.0.0", port=3000, workers=1, loop="none")
    )
    asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
