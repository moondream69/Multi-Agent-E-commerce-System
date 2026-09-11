"""会话存储缝(issue #21 + spec #20):ConversationStore 内存替身语义 + 会话端点流程(离线,不触 PG)。

替身挂在 InMemorySessionMemory 上(生产里 conversations 表由 memory.record 写、conversation_store 读,
共用同一行集);端点路由经 create_app 注入替身(JWT 直签,认证态,反证不绕过注入)。
真 PG 的惰性落库/命名/越权隔离语义见 test_conversations.py(integration)。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from python_backend.api.app import create_app
from python_backend.core.auth import create_token
from python_backend.core.graph import build_supervisor
from tests.conftest import (
    FailingConversationStore,
    InMemoryApprovalBatchStore,
    InMemorySessionMemory,
    InMemoryTaskStore,
    StubPlanner,
    slice_agent,
)


def _client(
    user_id: int,
    memory: InMemorySessionMemory,
    task_store: InMemoryTaskStore,
    batch_store: InMemoryApprovalBatchStore,
) -> TestClient:
    """认证态客户端:JWT 直签(不触登录端点/不触 PG),会话与任务都落在注入替身上。"""
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent([], actions=[], answer="切片完成")},
        checkpointer=InMemorySaver(),
        batch_store=batch_store,
    )
    client = TestClient(
        create_app(
            graph=graph,
            batch_store=batch_store,
            memory=memory,
            conversation_store=memory,
            task_store=task_store,
            auth_required=False,
        )
    )
    client.headers.update({"Authorization": f"Bearer {create_token('tester', user_id)}"})
    return client


# —— 内存替身可见语义(与生产同口径) ——


async def test_list_newest_first_and_lazy_creation() -> None:
    """列表最新在前(updated_at 倒序);record 即建行(同一 (user, session) 只一行,消息累积);他人会话不可见。"""
    memory = InMemorySessionMemory()
    await memory.record("s-1", 7, role="user", content="第一句")
    await memory.record("s-2", 7, role="user", content="第二会话")
    await memory.record("s-1", 7, role="assistant", content="回第一句")
    await memory.record("s-other", 8, role="user", content="他人会话")

    listing = await memory.list_conversations(7)
    assert [item["sessionId"] for item in listing] == ["s-1", "s-2"], "最后一次写入的会话在前"
    assert listing[0]["messageCount"] == 2
    assert listing[0]["title"] == "第一句"  # 建行时取首条消息前 20 字
    assert listing[0]["updatedAt"]


async def test_rename_bumps_order_keeps_owner_scope() -> None:
    """改名置新标题并前移 updated_at;不存在/非本人返回 None(端点映射 404)。"""
    memory = InMemorySessionMemory()
    await memory.record("s-1", 7, role="user", content="旧标题来源")
    await memory.record("s-2", 7, role="user", content="另一个会话")

    renamed = await memory.rename_conversation(7, "s-1", "手工名字")
    assert renamed is not None and renamed["title"] == "手工名字"
    assert [item["sessionId"] for item in await memory.list_conversations(7)] == ["s-1", "s-2"], "改名前移"

    assert await memory.rename_conversation(7, "s-missing", "x") is None
    assert await memory.rename_conversation(8, "s-1", "越权改名") is None, "非本人不生效"
    assert (await memory.list_conversations(7))[0]["title"] == "手工名字"


async def test_delete_is_owner_scoped() -> None:
    """删除仅对本人生效:他人删同名单返回 False(行仍在)。"""
    memory = InMemorySessionMemory()
    await memory.record("s-1", 7, role="user", content="待删会话")

    assert await memory.delete_conversation(8, "s-1") is False, "非本人删不掉"
    assert await memory.delete_conversation(7, "s-1") is True
    assert await memory.delete_conversation(7, "s-1") is False, "重复删除幂等 False"
    assert await memory.list_conversations(7) == []


async def test_has_pending_batches_derives_from_task_and_batch_stores() -> None:
    """挂起判定读注入的任务/批次真源:无批次 False、批次挂起 True、决定后回落 False。"""
    task_store = InMemoryTaskStore()
    batch_store = InMemoryApprovalBatchStore()
    memory = InMemorySessionMemory(task_store=task_store, batch_store=batch_store)
    await task_store.create_task_row(thread_id="t-1", user_id=7, session_id="s-1", type_="chat", request="请求")

    assert await memory.has_pending_batches(7, "s-1") is False, "无批次不拒删"
    batch = await batch_store.create_batch(
        batch_id="b-1", thread_id="t-1", slice_no=1, action_type="product.publish", actions=[], mode="approval"
    )
    assert await memory.has_pending_batches(7, "s-1") is True

    await batch_store.decide_batch(batch_id=batch.batch_id, decision="approve")
    assert await memory.has_pending_batches(7, "s-1") is False, "已决定即不再挂起"


async def test_get_messages_keeps_storage_order_and_maps_task_id() -> None:
    """消息流(spec #20)原序返回(落库序=时间序);task_id → taskId 透传;元数据与列表同形。"""
    memory = InMemorySessionMemory()
    await memory.record("s-1", 7, role="user", content="第一问", task_id="t-1")
    await memory.record("s-1", 7, role="user", content="第二问")
    await memory.record("s-1", 7, role="assistant", content="助手答复", task_id="t-1")

    stream = await memory.get_messages(7, "s-1")

    assert stream is not None
    assert [item["content"] for item in stream["messages"]] == ["第一问", "第二问", "助手答复"]
    assert [item["role"] for item in stream["messages"]] == ["user", "user", "assistant"]
    assert set(stream["messages"][0]) == {"role", "content", "timestamp", "taskId"}
    assert stream["messages"][0]["taskId"] == "t-1"
    assert stream["messages"][1]["taskId"] is None, "无任务归属的消息 taskId 为 null"
    assert stream["messages"][0]["timestamp"], "时间戳随行透传"
    assert set(stream["conversation"]) == {"sessionId", "title", "updatedAt", "messageCount"}
    assert stream["conversation"]["title"] == "第一问", "建行时写首条消息前 20 字"
    assert stream["conversation"]["messageCount"] == 3


async def test_get_messages_is_owner_scoped() -> None:
    """归属隔离:非本人/不存在 → None(端点映射 404),不泄漏他人消息。"""
    memory = InMemorySessionMemory()
    await memory.record("s-1", 7, role="user", content="甲的消息")

    assert await memory.get_messages(8, "s-1") is None, "非本人读不到"
    assert await memory.get_messages(7, "s-missing") is None, "不存在同样 None"


# —— 端点流程(认证态,注入替身) ——


def _stores() -> tuple[InMemorySessionMemory, InMemoryTaskStore, InMemoryApprovalBatchStore]:
    task_store, batch_store = InMemoryTaskStore(), InMemoryApprovalBatchStore()
    return InMemorySessionMemory(task_store=task_store, batch_store=batch_store), task_store, batch_store


async def test_conversation_endpoints_use_injected_store() -> None:
    """列表/改名/删除都读注入替身(预置探针会话可见即反证未绕过注入直连 PG),404 分支同形。"""
    memory, task_store, batch_store = _stores()
    await memory.record("probe-session", 42, role="user", content="注入替身的探针会话")
    client = _client(42, memory, task_store, batch_store)

    listing = client.get("/api/conversations").json()
    assert [item["sessionId"] for item in listing["conversations"]] == ["probe-session"]
    assert set(listing["conversations"][0]) == {"sessionId", "title", "updatedAt", "messageCount"}

    renamed = client.patch("/api/conversations/probe-session", json={"title": "  改名  "})
    assert renamed.status_code == 200
    assert renamed.json()["conversation"]["title"] == "改名", "trim 后落替身"

    assert client.patch("/api/conversations/probe-session", json={"title": "   "}).status_code == 422
    assert client.patch("/api/conversations/probe-session", json={"title": "长" * 51}).status_code == 422
    assert client.patch("/api/conversations/s-missing", json={"title": "x"}).status_code == 404

    assert client.delete("/api/conversations/probe-session").json() == {"deleted": True}
    assert client.delete("/api/conversations/probe-session").status_code == 404
    assert client.get("/api/conversations").json()["conversations"] == []


async def test_task_flow_creates_conversation_and_pending_approval_blocks_delete() -> None:
    """发起任务即经 memory.record 建会话(标题 20 字截断);该线程出现挂起批次后删除 409。"""
    memory, task_store, batch_store = _stores()
    client = _client(42, memory, task_store, batch_store)

    created = client.post(
        "/api/tasks", json={"request": "发起的任务请求标题超过二十个字用于截断断言", "session_id": "s-flow"}
    )
    assert created.status_code == 201
    thread_id = created.json()["thread_id"]

    conversations = client.get("/api/conversations").json()["conversations"]
    assert [item["sessionId"] for item in conversations] == ["s-flow"]
    assert conversations[0]["title"] == "发起的任务请求标题超过二十个字用于截断断言"[:20]
    assert conversations[0]["messageCount"] >= 1

    # 直接经替身造挂起批次(生产由图落库):挂起期间拒删,决定后放行
    batch = await batch_store.create_batch(
        batch_id="b-block", thread_id=thread_id, slice_no=1, action_type="product.publish", actions=[], mode="approval"
    )
    blocked = client.delete("/api/conversations/s-flow")
    assert blocked.status_code == 409
    assert "挂起审批" in blocked.json()["detail"]
    assert [item["sessionId"] for item in client.get("/api/conversations").json()["conversations"]] == ["s-flow"]

    await batch_store.decide_batch(batch_id=batch.batch_id, decision="approve")
    assert client.delete("/api/conversations/s-flow").json() == {"deleted": True}


async def test_conversations_are_scoped_to_token_user() -> None:
    """会话按 JWT 用户隔离:乙看不到甲的会话,也不能删甲的(404)。"""
    memory, task_store, batch_store = _stores()
    await memory.record("s-mine", 42, role="user", content="甲的消息")
    client_a = _client(42, memory, task_store, batch_store)
    client_b = _client(99, memory, task_store, batch_store)

    assert [item["sessionId"] for item in client_a.get("/api/conversations").json()["conversations"]] == ["s-mine"]
    assert client_b.get("/api/conversations").json()["conversations"] == []
    assert client_b.delete("/api/conversations/s-mine").status_code == 404
    assert [item["sessionId"] for item in client_a.get("/api/conversations").json()["conversations"]] == ["s-mine"]


# —— 消息流端点(spec #20 A2 延伸) ——


async def test_messages_endpoint_reads_injected_store() -> None:
    """GET /messages 读注入替身(探针消息可见即反证未绕过注入),原序 + taskId 透传 + 元数据同列。"""
    memory, task_store, batch_store = _stores()
    await memory.record("probe-session", 42, role="user", content="探针提问", task_id="t-probe")
    await memory.record("probe-session", 42, role="assistant", content="探针答复", task_id="t-probe")
    client = _client(42, memory, task_store, batch_store)

    response = client.get("/api/conversations/probe-session/messages")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"conversation", "messages"}
    assert set(body["conversation"]) == {"sessionId", "title", "updatedAt", "messageCount"}
    assert body["conversation"]["sessionId"] == "probe-session"
    assert body["conversation"]["messageCount"] == 2
    assert [item["content"] for item in body["messages"]] == ["探针提问", "探针答复"]
    assert [item["role"] for item in body["messages"]] == ["user", "assistant"]
    assert [item["taskId"] for item in body["messages"]] == ["t-probe", "t-probe"]
    assert set(body["messages"][0]) == {"role", "content", "timestamp", "taskId"}


async def test_messages_endpoint_404_for_missing_or_other_user() -> None:
    """不存在/非本人 → 404(归属校验与 PATCH/DELETE 同语义);他人读不泄漏消息。"""
    memory, task_store, batch_store = _stores()
    await memory.record("s-mine", 42, role="user", content="甲的消息")
    client_a = _client(42, memory, task_store, batch_store)
    client_b = _client(99, memory, task_store, batch_store)

    assert client_a.get("/api/conversations/s-mine/messages").status_code == 200
    assert client_b.get("/api/conversations/s-mine/messages").status_code == 404
    assert client_a.get("/api/conversations/s-missing/messages").status_code == 404


async def test_messages_endpoint_unauthenticated_404_skips_store() -> None:
    """未认证(无 token)→ 404 且零存储调用:必炸替身反证——触库即 500,404 即未触。"""
    client = TestClient(create_app(auth_required=False, conversation_store=FailingConversationStore()))

    response = client.get("/api/conversations/s-any/messages")

    assert response.status_code == 404
