"""uvicorn 入口:python_backend.main:app(端口 3000,由 api/app.py 组装)。"""

from python_backend.api.app import make_asgi

app = make_asgi()
