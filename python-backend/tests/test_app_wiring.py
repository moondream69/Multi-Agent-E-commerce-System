"""生产装配启动回归(issue #21):build_app 的 lifespan 必须把会话存储接到批真实例上。

回归背景:会话存储的挂起审批判定依赖批次存储,而 create_app() 构建时 lifespan 尚未跑
(批真实例在 lifespan 内创建)——曾漏接线,app.state.conversation_store 留 None,
生产所有会话端点 500,而离线用例全部显式注入替身、不会红。本文件补「默认装配」守卫。

离线可跑:仅打桩外部依赖(PG 连接 / checkpoint saver / Agent 装配),被验证的接线是真代码;
真实 seed 与真库启动另有实证(见 issue #21 评论)。
"""

from __future__ import annotations

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

import python_backend.main as main_module
from python_backend.db.approval_store import PostgresApprovalBatchStore
from python_backend.db.conversation_store import PostgresConversationStore
from python_backend.db.customer_store import PostgresCustomerStore


class _StubConnection:
    async def close(self) -> None:
        return None


class _StubSaver:
    """AsyncPostgresSaver 桩:吞掉 conn/serde,setup 空转(checkpoint 表建表属集成面)。"""

    def __init__(self, _conn, *, serde) -> None:
        self._serde = serde

    async def setup(self) -> None:
        return None


async def _stub_agents() -> dict:
    return {}


async def _stub_setup(_self) -> None:
    return None


async def _stub_seed() -> None:
    return None


async def _stub_seed_method(_self) -> None:
    return None  # 两个启动 seed 的落库面属集成测试;此处只验证「被调用 + 接线对象正确」


def test_build_app_lifespan_wires_conversation_store(monkeypatch) -> None:
    """lifespan 启动后:会话存储已接批真实例,不再留 None。

    不请求 /health:该端点在 build_app 内绑定 `_ping_db`,改写模块属性不生效(闭包已捕获);
    TestClient 上下文进出本身即证明 lifespan 启动/关闭走完。
    """

    async def _connect(*_args, **_kwargs) -> _StubConnection:
        return _StubConnection()

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", _connect)
    monkeypatch.setattr(AsyncPostgresSaver, "setup", _stub_setup)
    monkeypatch.setattr(main_module, "build_agents", _stub_agents)
    monkeypatch.setattr(main_module, "ensure_admin_user", _stub_seed)
    # 买家 seed 经 lifespan 调 app.state.customer_store(默认真实例):只桩掉其落库动作
    monkeypatch.setattr(PostgresCustomerStore, "ensure_demo_buyers", _stub_seed_method)

    app = main_module.build_app()
    # socketio.ASGIApp 包裹的内层 FastAPI;断言兼作类型收窄(stubs 标为可能 None)
    fastapi_app = app.other_asgi_app
    assert isinstance(fastapi_app, FastAPI), "build_app 恒包裹 create_app 的 FastAPI 实例"
    assert fastapi_app.state.conversation_store is None, "构造期批真实例未就绪,接线必须发生在 lifespan"

    with TestClient(app):
        store = fastapi_app.state.conversation_store
        assert isinstance(store, PostgresConversationStore), "生产启动必须把会话存储接上(回归 #21)"
        assert isinstance(fastapi_app.state.batch_store, PostgresApprovalBatchStore)
        assert store._batch_store is fastapi_app.state.batch_store, "会话存储须与图共用同一批真实例"
