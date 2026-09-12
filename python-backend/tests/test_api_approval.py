"""REST 端点(spec #7):任务发起/全量与按线程批次列表/多批一次决定/自然消息/影子补执行。

接缝:HTTP 接口(create_app 注入图/批次存储/任务行存储)与意图判定纯函数;
任务行注入 InMemoryTaskStore,离线快速套件可跑(issue #13 缝)。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from python_backend.agents.executor import ApplyResult
from python_backend.api.app import create_app
from python_backend.core.approvals import parse_decision_intent
from python_backend.core.auth import create_token
from python_backend.core.graph import build_supervisor
from python_backend.db.models import TaskStatus
from tests.conftest import (
    FailingNotificationStore,
    FakeApply,
    InMemoryApprovalBatchStore,
    InMemoryNotificationStore,
    InMemorySessionMemory,
    InMemoryTaskStore,
    RecordingEmitter,
    StubPlanner,
    slice_agent,
)

PUBLISH = {
    "action": "product.publish",
    "params": {"product_id": 1},
    "snapshot": {"exists": True, "status": "draft"},
}


class _EffectApply:
    """脚本化 apply:固定效果描述(通知路径用例共用)。"""

    def __init__(self, effects: list[dict]) -> None:
        self.effects = effects

    async def __call__(self, batch_id: str, actions: list[dict]) -> ApplyResult:
        return ApplyResult(applied=True, effects=self.effects)


def make_client(*, shadow_mode: bool = False) -> tuple[TestClient, InMemoryApprovalBatchStore, FakeApply]:
    store = InMemoryApprovalBatchStore()
    apply_fn = FakeApply()
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent(actions=[PUBLISH], answer="上架动作已登记")},
        checkpointer=InMemorySaver(),
        batch_store=store,
        shadow_mode=shadow_mode,
        apply_fn=apply_fn,
    )
    return (
        TestClient(
            create_app(
                graph=graph,
                batch_store=store,
                apply_fn=apply_fn,
                memory=InMemorySessionMemory(),  # 默认件是 PG 记忆;离线快速套件须注入内存实现
                task_store=InMemoryTaskStore(),  # 默认件是 PG 任务行存储;同上(issue #13)
                auth_required=False,
                shadow_mode=shadow_mode,  # issue #37:剖面随图——影子段可见性与补执行同源
            )
        ),
        store,
        apply_fn,
    )


def make_profile_client(*, shadow_mode: bool) -> tuple[TestClient, InMemoryApprovalBatchStore, FakeApply]:
    """剖面过滤用例客户端(issue #37,验收 B15):空图,只需批次存储 + /api/approvals 与补执行两个端点。"""
    store = InMemoryApprovalBatchStore()
    apply_fn = FakeApply()
    client = TestClient(
        create_app(
            batch_store=store,
            apply_fn=apply_fn,
            memory=InMemorySessionMemory(),  # 同 make_client:离线快速套件不触 PG
            task_store=InMemoryTaskStore(),
            auth_required=False,
            shadow_mode=shadow_mode,
        )
    )
    return client, store, apply_fn


async def test_create_task_interrupts_with_batches() -> None:
    """POST /api/tasks:带审批动作的任务发起后状态 interrupted。"""
    client, _store, _apply = make_client()
    response = client.post("/api/tasks", json={"request": "上架商品"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "interrupted"
    assert body["threadId"]


async def test_approvals_envelope_carries_thread_plan_context() -> None:
    """信封 `plans` 旁挂:原始需求 + 切片计划(ADR-0005 任务上下文+后续计划预览;spec #34)。

    任务行须认证上下文才落库(user_id None 跳过),故本用例显式带 token。
    """
    client, _store, _apply = make_client()
    client.headers.update({"Authorization": f"Bearer {create_token('tester', 42)}"})
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]

    body = client.get(f"/api/threads/{thread_id}/approvals").json()
    global_body = client.get("/api/approvals").json()

    assert set(body) == {"approvals", "plans"} and set(global_body) == {"approvals", "plans"}
    assert body["plans"].keys() == {thread_id}
    entry = body["plans"][thread_id]
    assert entry["request"] == "上架商品", "任务上下文 = 原始需求"
    assert [item["no"] for item in entry["plan"]["slices"]] == [1], "后续计划 = 切片计划(与详情 plan 同形)"
    assert entry["plan"]["slices"][0]["agent"] == "order_management"
    assert global_body["plans"] == body["plans"], "全局与按线程两个入口同源"


async def test_suspended_task_row_carries_plan() -> None:
    """挂起期任务行即落切片计划(spec #34 修复):否则审批中心计划预览恒空、
    驾驶舱切片时间线在「等待审批」时显示 0 段且无「去审批」入口。"""
    client, _store, _apply = make_client()
    client.headers.update({"Authorization": f"Bearer {create_token('tester', 42)}"})
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]

    detail = client.get(f"/api/tasks/{thread_id}").json()

    assert detail["status"] == "interrupted"
    assert detail["plan"] is not None, "挂起期计划不得为空(终态才写的旧行为已修)"
    assert [item["no"] for item in detail["plan"]["slices"]] == [1]
    assert detail["plan"]["slices"][0]["description"] == "上架商品"


async def test_approvals_envelope_omits_unknown_threads() -> None:
    """无任务行(未认证路径未落行)的线程不入 plans 旁挂 —— 前端按缺省不渲染计划区。"""
    client, _store, _apply = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]

    body = client.get("/api/approvals").json()

    assert body["plans"] == {}, "无行不伪造上下文"
    assert [batch["threadId"] for batch in body["approvals"]] == [thread_id], "批次本体不受影响"


async def test_list_open_and_per_thread_approvals() -> None:
    """GET /api/approvals(全局)+ GET /api/threads/{id}/approvals:真实参数快照形状。"""
    client, store, _apply = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]

    response = client.get("/api/approvals")
    approvals = response.json()["approvals"]
    assert len(approvals) == 1
    batch = approvals[0]
    assert batch["status"] == "pending"
    assert batch["sliceNo"] == 1
    assert batch["actionType"] == "product.publish"
    assert batch["actions"][0]["action"] == "product.publish"
    assert batch["actions"][0]["params"] == {"product_id": 1}
    assert batch["actions"][0]["snapshot"]["status"] == "draft"

    per_thread = client.get(f"/api/threads/{thread_id}/approvals").json()["approvals"]
    assert [b["batchId"] for b in per_thread] == [batch["batchId"]]
    stored = await store.get_batch(batch_id=batch["batchId"])
    assert stored is not None and stored.status == "pending"


async def test_resume_approve_completes_and_applies() -> None:
    """POST /resume(按钮入口):approve → apply 被调(接缝)→ 任务完成。"""
    client, store, apply_fn = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]
    batch_id = (await store.list_pending(thread_id))[0].batch_id

    response = client.post(f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "approve"}})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["summary"] == "完成 1/1 个切片"  # issue #25:摘要回归字符串口径
    assert [bid for bid, _a in apply_fn.calls] == [batch_id]
    assert await store.list_pending(thread_id) == []


async def test_resume_completes_when_notification_store_fails() -> None:
    """增量 8-T1:apply 后通知落库失败按辅助簿记分类——任务照常 completed(任务行不被翻)、响应 200、广播照常。"""
    store = InMemoryApprovalBatchStore()
    emitter = RecordingEmitter()
    task_store = InMemoryTaskStore()
    apply_fn = _EffectApply([{"type": "order_status", "order_id": 3, "from": "pending", "to": "confirmed"}])
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent(actions=[PUBLISH], answer="已登记")},
        checkpointer=InMemorySaver(),
        batch_store=store,
        apply_fn=apply_fn,
        emitter=emitter,  # 生产装配:图与端点共用同一发射器(main.py 同款)
    )
    client = TestClient(
        create_app(
            graph=graph,
            batch_store=store,
            apply_fn=apply_fn,
            emitter=emitter,
            memory=InMemorySessionMemory(),
            task_store=task_store,
            notification_store=FailingNotificationStore(),
            auth_required=False,
        )
    )
    client.headers.update({"Authorization": f"Bearer {create_token('tester', 42)}"})

    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]
    batch_id = (await store.list_pending(thread_id))[0].batch_id

    response = client.post(f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "approve"}})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    row = await task_store.get_task(thread_id)
    assert row is not None and row.status is TaskStatus.COMPLETED, "通知落库失败不得翻转任务行"
    assert [event for event, _payload in emitter.events if event == "notification.created"] == [
        "notification.created"
    ], "落库失败不阻塞广播"


async def test_resume_reject_updates_batch_and_replans() -> None:
    """POST /resume:reject → 批次落 rejected,回流后按冲突终止(StubPlanner 静态计划)。"""
    client, store, apply_fn = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]
    batch_id = (await store.list_pending(thread_id))[0].batch_id

    response = client.post(
        f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "reject", "comment": "价格太低"}}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed", "冲突终止以 failed 如实呈现(与行/事件一致)"
    batch = await store.get_batch(batch_id=batch_id)
    assert batch is not None
    assert batch.status == "rejected"
    assert batch.comment == "价格太低"
    assert apply_fn.calls == []
    assert body["error"], "重规划冲突应如实上报「未完成+原因」"


async def test_resume_unknown_batch_is_404() -> None:
    """resume 不存在的批次 → 404。"""
    client, _store, _apply = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]
    response = client.post(f"/api/threads/{thread_id}/resume", json={"no-such-batch": {"decision": "approve"}})
    assert response.status_code == 404


async def test_resume_partial_body_is_422() -> None:
    """部分决定被拒绝(422):未决定批次会静默按拒处理,必须一次提交全部决定。"""
    client, _store, _apply = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]
    response = client.post(f"/api/threads/{thread_id}/resume", json={})
    assert response.status_code == 422
    assert "全部" in response.json()["detail"]


async def test_resume_without_pending_is_409() -> None:
    """无挂起中断时 resume → 409。"""
    client, store, _apply = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]
    batch_id = (await store.list_pending(thread_id))[0].batch_id
    client.post(f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "approve"}})
    response = client.post(f"/api/threads/{thread_id}/resume", json={batch_id: {"decision": "approve"}})
    assert response.status_code == 409


async def test_natural_message_approve_resumes_all_batches() -> None:
    """POST /message:「同意」→ 全部挂起批次 approve → 完成。"""
    client, store, apply_fn = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]

    response = client.post(f"/api/threads/{thread_id}/message", json={"text": "同意"})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert len(apply_fn.calls) == 1
    assert await store.list_pending(thread_id) == []


async def test_natural_message_without_pending_is_409() -> None:
    """无挂起批次时发决定消息 → 409。"""
    client, _store, _apply = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]
    client.post(f"/api/threads/{thread_id}/message", json={"text": "同意"})
    response = client.post(f"/api/threads/{thread_id}/message", json={"text": "同意"})
    assert response.status_code == 409


async def test_natural_message_terminate_ends_thread_without_replan() -> None:
    """POST /message「算了」→ terminate:批次落 rejected、图「用户终止」结束、不回流重规划。"""
    client, store, apply_fn = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]

    response = client.post(f"/api/threads/{thread_id}/message", json={"text": "算了"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed", "终止以 failed 终态如实呈现(与行/事件一致)"
    assert body["error"] == "用户终止任务"
    batch = store.batches[0]
    assert batch.status == "rejected"
    assert "算了" in (batch.comment or "")
    assert apply_fn.calls == []


async def test_unrecognized_intent_is_422() -> None:
    """无法识别的自然消息 → 422。"""
    client, _store, _apply = make_client()
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]
    response = client.post(f"/api/threads/{thread_id}/message", json={"text": "今天天气不错"})
    assert response.status_code == 422


async def test_shadow_batch_execute_endpoint() -> None:
    """A15:影子批次一键补执行——execute 端点调 apply,批次落 executed。"""
    client, store, apply_fn = make_client(shadow_mode=True)
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]
    assert client.post("/api/tasks", json={"request": "上架商品"}).json()["status"] == "completed"

    batch = (await store.list_open())[0]
    assert batch.mode == "shadow"

    response = client.post(f"/api/threads/{thread_id}/shadow-batches/{batch.batch_id}/execute")
    assert response.status_code == 200
    assert response.json()["status"] == "executed"
    assert [bid for bid, _a in apply_fn.calls] == [
        batch.batch_id
    ]  # 批次状态落 executed 由真实 apply 负责(此处注入假实现)


async def test_shadow_execute_unknown_batch_is_404() -> None:
    """演练剖面下未知批次 404;生产剖面整体 403(剖面闸先于存在性,见下一用例)。"""
    client, _store, _apply = make_client(shadow_mode=True)
    response = client.post("/api/threads/whatever/shadow-batches/no-such/execute")
    assert response.status_code == 404


async def test_approvals_hides_shadow_batches_in_prod_profile() -> None:
    """issue #37(验收 B15):生产剖面审批中心不显示影子段——只返回 pending;前端零剖面感知。"""
    client, store, _apply = make_profile_client(shadow_mode=False)
    await store.create_batch(
        batch_id="a-1", thread_id="t-1", slice_no=1, action_type="product.publish", actions=[PUBLISH], mode="approval"
    )
    await store.create_batch(
        batch_id="s-1", thread_id="t-1", slice_no=2, action_type="product.publish", actions=[PUBLISH], mode="shadow"
    )

    body = client.get("/api/approvals").json()

    assert [batch["batchId"] for batch in body["approvals"]] == ["a-1"]


async def test_approvals_shows_shadow_batches_in_dev_profile() -> None:
    """演练剖面:影子段照常返回——前端「影子」标签与补执行按钮的数据源。"""
    client, store, _apply = make_profile_client(shadow_mode=True)
    await store.create_batch(
        batch_id="a-1", thread_id="t-1", slice_no=1, action_type="product.publish", actions=[PUBLISH], mode="approval"
    )
    await store.create_batch(
        batch_id="s-1", thread_id="t-1", slice_no=2, action_type="product.publish", actions=[PUBLISH], mode="shadow"
    )

    body = client.get("/api/approvals").json()

    assert [batch["batchId"] for batch in body["approvals"]] == ["a-1", "s-1"]
    assert [batch["mode"] for batch in body["approvals"]] == ["approval", "shadow"]


async def test_shadow_execute_refused_in_prod_profile() -> None:
    """issue #37:生产剖面补执行如实 403——影子批次未经人工批准,补执行即绕过审批锁;
    拦截先于 apply,批次状态不被触碰。"""
    client, store, apply_fn = make_profile_client(shadow_mode=False)
    batch = await store.create_batch(
        batch_id="s-1", thread_id="t-1", slice_no=1, action_type="product.publish", actions=[PUBLISH], mode="shadow"
    )

    response = client.post("/api/threads/t-1/shadow-batches/s-1/execute")

    assert response.status_code == 403
    assert apply_fn.calls == [], "拦截先于 apply"
    assert batch.status == "shadow", "批次状态不被触碰"


async def test_shadow_execute_refused_in_prod_profile_even_for_unknown_batch() -> None:
    """剖面闸先于存在性:生产剖面下未知批次同为 403(能力整体关闭,不区分存在性)。"""
    client, _store, _apply = make_profile_client(shadow_mode=False)
    response = client.post("/api/threads/t-1/shadow-batches/no-such/execute")
    assert response.status_code == 403


async def test_shadow_batch_execute_emits_notifications() -> None:
    """spec #9 + 增量 8-T1:影子补执行提交后按 apply 效果落库(扇出)并广播通知(与切片 apply 同一语义)。"""
    store = InMemoryApprovalBatchStore()
    emitter = RecordingEmitter()
    notification_store = InMemoryNotificationStore()
    apply_fn = _EffectApply([{"type": "order_status", "order_id": 9, "from": "pending", "to": "confirmed"}])
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent(actions=[PUBLISH], answer="已登记")},
        checkpointer=InMemorySaver(),
        batch_store=store,
        shadow_mode=True,
        apply_fn=apply_fn,
    )
    client = TestClient(
        create_app(
            graph=graph,
            batch_store=store,
            apply_fn=apply_fn,
            emitter=emitter,
            memory=InMemorySessionMemory(),  # 同上:避免默认 PG 记忆触库
            task_store=InMemoryTaskStore(),  # 同上:避免默认 PG 任务行存储触库
            notification_store=notification_store,  # 同上:避免默认 PG 通知存储触库(增量 8-T1)
            auth_required=False,
            shadow_mode=True,  # issue #37:本用例走演练剖面补执行
        )
    )
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]
    batch = (await store.list_open())[0]

    response = client.post(f"/api/threads/{thread_id}/shadow-batches/{batch.batch_id}/execute")

    assert response.status_code == 200
    notifications = [payload for event, payload in emitter.events if event == "notification.created"]
    assert len(notifications) == 1
    assert notifications[0]["kind"] == "order_status"
    assert "#9" in notifications[0]["message"]
    assert len(notification_store.rows) == 1, "影子补执行的通知同样落库(默认扇出 1 用户)"
    assert notification_store.rows[0]["kind"] == "order_status"


async def test_shadow_batch_execute_survives_notification_store_failure() -> None:
    """增量 8-T1:落库失败按辅助簿记分类——端点如实 200、广播照常(spec #11 分类 ③)。"""
    store = InMemoryApprovalBatchStore()
    emitter = RecordingEmitter()
    apply_fn = _EffectApply([{"type": "order_status", "order_id": 9, "from": "pending", "to": "confirmed"}])
    graph = build_supervisor(
        StubPlanner(),
        agents={"order_management": slice_agent(actions=[PUBLISH], answer="已登记")},
        checkpointer=InMemorySaver(),
        batch_store=store,
        shadow_mode=True,
        apply_fn=apply_fn,
    )
    client = TestClient(
        create_app(
            graph=graph,
            batch_store=store,
            apply_fn=apply_fn,
            emitter=emitter,
            memory=InMemorySessionMemory(),
            task_store=InMemoryTaskStore(),
            notification_store=FailingNotificationStore(),
            auth_required=False,
            shadow_mode=True,  # issue #37:同上,演练剖面补执行
        )
    )
    thread_id = client.post("/api/tasks", json={"request": "上架商品"}).json()["threadId"]
    batch = (await store.list_open())[0]

    response = client.post(f"/api/threads/{thread_id}/shadow-batches/{batch.batch_id}/execute")

    assert response.status_code == 200
    assert [event for event, _payload in emitter.events if event == "notification.created"] == [
        "notification.created"
    ], "落库失败不阻塞广播"


def test_parse_decision_intent_priority() -> None:
    """意图判定:terminate > reject > approve(「不同意」不得误判为 approve)。"""
    assert parse_decision_intent("同意") == "approve"
    assert parse_decision_intent("不同意") == "reject"
    assert parse_decision_intent("不通过") == "reject"
    assert parse_decision_intent("取消任务") == "terminate"
    assert parse_decision_intent("今天天气不错") is None
