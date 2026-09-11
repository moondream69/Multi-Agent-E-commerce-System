"""测试共享夹具与工具:外部服务探测 + 图级单测共享脚手架(store/规划器/切片执行桩/LLM/向量)。"""

from __future__ import annotations

import math
import re
import socket
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlparse

import pytest
from sqlalchemy import Enum

from python_backend.agents.executor import ApplyResult
from python_backend.core.approvals import (
    ApprovalBatchRecord,
    BatchAlreadyDecidedError,
    decided_status,
    initial_status,
)
from python_backend.core.planning import Slice, SlicePlan
from python_backend.db.base import Base
from python_backend.db.customer_store import DEMO_BUYERS
from python_backend.db.models import (
    Conversation,
    Customer,
    Order,
    Product,
    Task,
    TaskStatus,
    Ticket,
    TicketStatus,
)
from python_backend.db.notification_store import MAX_PER_KIND
from python_backend.db.report_store import REVENUE_WINDOW_DAYS
from python_backend.infrastructure.llm import ToolCallResult
from python_backend.settings import get_settings
from python_backend.vector_repo.base import SearchHit, VectorRecord, VectorRepository


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def milvus_reachable(uri: str, timeout: float = 2.0) -> bool:
    """Milvus 在线 TCP 快速探测:gRPC 连接失败的重试放大很慢,先探端口再构造客户端。"""
    parsed = urlparse(uri)
    return _tcp_reachable(parsed.hostname or "", parsed.port or 19530, timeout)


def postgres_reachable(database_url: str, timeout: float = 2.0) -> bool:
    """Postgres 在线 TCP 快速探测:离线时 integration 秒 skip(默认超时放大至分钟级)。"""
    parsed = urlparse(database_url.replace("postgresql+psycopg://", "postgresql://"))
    return _tcp_reachable(parsed.hostname or "", parsed.port or 5432, timeout)


def require_postgres() -> None:
    """Postgres 在线探测:离线秒 skip(各测试模块与夹具共用,勿再复制本地版本)。"""
    if not postgres_reachable(get_settings().database_url):
        pytest.skip("Postgres 离线(compose dev 库),integration 跳过")


@pytest.fixture
def requires_postgres() -> None:
    """integration 模块共享夹具(经 pytestmark usefixtures 挂载,等价原先各文件的 autouse 探测)。"""
    require_postgres()


def assert_enum_columns_declared_safely() -> list[str]:
    """枚举列声明面守卫(issue #22,离线):遍历 ``Base.metadata`` 自动发现全部 ``sa.Enum`` 列并断言口径。

    口径 = ``db/models.py`` 的 ``_status_column_type()``(新增枚举列照该模式照抄);自动发现使
    「新增枚举列即被守卫」,取代 #12 遗留的硬编码五表清单。范围取**全部枚举列**(不按 NOT NULL
    或列名 status 筛选):原生枚举在多行批插渲染 ``::<名>`` cast,任何枚举列都须排除。

    - ``native_enum is False`` —— 库内 VARCHAR,批插不 cast;
    - 落库值 = 成员 ``.value``(小写口径)——期望自枚举成员取、不写字面量;缺 ``values_callable``
      时 SQLAlchemy 取成员 name(大写),此处即报;
    - 类型名 = 枚举类名的 snake_case —— 迁移 0001 五处先例(product_status / order_status / ...);
      类名含连续大写缩写(如 ``SKUStatus``)时本规则会误判,届时改类名或放宽此处。

    返回发现的列位置串(``表.列``),供调用方断言发现非空(防遍历失效导致守卫空转)。
    """
    discovered: list[str] = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            col_type = column.type
            if not isinstance(col_type, Enum):
                continue
            where = f"{table.name}.{column.name}"
            discovered.append(where)
            assert col_type.native_enum is False, f"{where}: 枚举列须 native_enum=False(原生枚举批插会渲染 cast)"
            enum_class = col_type.enum_class
            assert enum_class is not None, f"{where}: 须以枚举类声明(字符串枚举无从校验 value 口径)"
            expected_values = [member.value for member in enum_class]
            assert list(col_type.enums) == expected_values, (
                f"{where}: 落库口径须为成员 value {expected_values},实为 {list(col_type.enums)}"
                " —— 照 _status_column_type() 补 values_callable"
            )
            expected_name = re.sub(r"(?<!^)(?=[A-Z])", "_", enum_class.__name__).lower()
            assert col_type.name == expected_name, (
                f"{where}: 类型名须随迁移命名约定 = 枚举类名 snake_case({expected_name}),实为 {col_type.name!r}"
            )
    return discovered


class InMemorySessionMemory:
    """会话记忆内存实现(B16 接缝;供离线可跑的测试注入,勿用于需复现摘要/跨进程持久化的用例)。

    get_context 恒返回 None:本替身不复现 PG 实现的摘要语义,需要该行为请走集成测试。
    同时是会话存储替身(issue #21):record 即会话惰性创建(建行时写 20 字标题,之后不覆盖),
    端点读路径经本对象的 list/rename/delete/has_pending_batches——生产里这两条路径分别由
    memory.record(conversations 表写)与 conversation_store(同表读)承担,共用一张行集,
    故内存侧合并为一个对象,跨进程语义(摘要/唯一约束)仍以集成测试为准绳。
    """

    def __init__(
        self,
        *,
        task_store: InMemoryTaskStore | None = None,
        batch_store: InMemoryApprovalBatchStore | None = None,
    ) -> None:
        self.records: list[dict] = []
        self._rows: dict[tuple[int, str], Conversation] = {}
        self._task_store = task_store  # has_pending_batches 的线程发现源(生产查 tasks 表)
        self._batch_store = batch_store  # 挂起判定真源(生产读注入的批次存储)

    async def get_context(self, session_id: str, user_id: int | None) -> str | None:
        return None

    async def record(
        self, session_id: str, user_id: int | None, *, role: str, content: str, task_id: str | None = None
    ) -> None:
        self.records.append(
            {"session_id": session_id, "user_id": user_id, "role": role, "content": content, "task_id": task_id}
        )
        if user_id is None or not content:
            return
        row = self._rows.get((user_id, session_id))
        if row is None:
            row = Conversation(
                user_id=user_id,
                session_id=session_id,
                title=content[:20],  # A2 语义:标题截断 20 字(建行时写一次)
                messages=[],
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self._rows[(user_id, session_id)] = row
        row.messages = [
            *row.messages,
            {"role": role, "content": content, "timestamp": datetime.now(UTC).isoformat(), "task_id": task_id},
        ]
        row.updated_at = datetime.now(UTC)

    async def list_conversations(self, user_id: int) -> list[dict]:
        rows = [row for (owner, _sid), row in self._rows.items() if owner == user_id]
        rows.sort(key=lambda row: row.updated_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return [
            {
                "sessionId": row.session_id,
                "title": row.title or "",
                "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
                "messageCount": len(row.messages or []),
            }
            for row in rows
        ]

    async def rename_conversation(self, user_id: int, session_id: str, title: str) -> dict | None:
        row = self._rows.get((user_id, session_id))
        if row is None:
            return None
        row.title = title  # 手工命名:自动标题只在建行时写,不被本方法触发的后续消息覆盖
        row.updated_at = datetime.now(UTC)
        return next(item for item in await self.list_conversations(user_id) if item["sessionId"] == session_id)

    async def delete_conversation(self, user_id: int, session_id: str) -> bool:
        return self._rows.pop((user_id, session_id), None) is not None

    async def has_pending_batches(self, user_id: int, session_id: str) -> bool:
        if self._task_store is None or self._batch_store is None:
            raise RuntimeError("InMemorySessionMemory 未接任务/批次存储,无法判定挂起审批(注入时传入)")
        threads = [
            row.thread_id
            for row in (await self._task_store.list_tasks(session_id=session_id))
            if row.user_id == user_id
        ]
        return any([await self._batch_store.list_pending(thread_id) for thread_id in threads])


class InMemoryCustomerStore:
    """买家存储内存实现(issue #21 接缝;端点与启动 seed 离线可跑,不触 PG)。

    复现生产可见语义:列表按创建倒序(插入倒排)、email 唯一(生产为唯一约束)、
    seed 仅 dev 且幂等(逐 email 判重,不覆盖既有行);rows 直接暴露供工单列表拼装买家名。
    """

    def __init__(
        self, *, settings_provider: Callable[[], object] = get_settings, demo_buyers: list[dict] | None = None
    ) -> None:
        self.rows: list[Customer] = []
        self._settings_provider = settings_provider  # 逐次读:测试可切换环境剖面(Settings 桩只需 environment 属性)
        self._demo_buyers = demo_buyers

    def add(self, name: str, email: str, locale: str = "zh-CN") -> Customer:
        customer = Customer(id=len(self.rows) + 1, name=name, email=email, locale=locale)
        self.rows.append(customer)
        return customer

    async def list_customers(self) -> list[dict]:
        ordered = list(reversed(self.rows))  # 插入倒排 = 最新在前(生产按 created_at 降序)
        return [{"customerId": row.id, "name": row.name, "email": row.email, "locale": row.locale} for row in ordered]

    async def ensure_demo_buyers(self) -> None:
        if getattr(self._settings_provider(), "environment", None) != "dev":
            return
        for buyer in self._demo_buyers or DEMO_BUYERS:
            if any(row.email == buyer["email"] for row in self.rows):
                continue
            self.add(buyer["name"], buyer["email"], buyer["locale"])

    async def find_name(self, customer_id: int) -> str | None:
        for row in self.rows:
            if row.id == customer_id:
                return row.name
        return None


class InMemoryTicketStore:
    """工单存储内存实现(issue #21 接缝;工单列表/结单端点离线可跑,不触 PG)。

    复现生产可见语义:创建倒序、买家名经注入的买家存储解析(无买家为 null)、
    结单 open→closed 记 resolved_at 且重复结单幂等(时间不变);created_at 由本实现填充(生产 server_default)。
    """

    def __init__(self, customers: InMemoryCustomerStore) -> None:
        self.rows: list[Ticket] = []
        self._customers = customers

    def add(self, message: str, *, created_by: str = "tester", customer_id: int | None = None) -> Ticket:
        ticket = Ticket(
            id=len(self.rows) + 1,
            message=message,
            created_by=created_by,
            customer_id=customer_id,
            status=TicketStatus.OPEN,
            created_at=datetime.now(UTC),
        )
        self.rows.append(ticket)
        return ticket

    async def list_tickets(self) -> list[dict]:
        ordered = list(reversed(self.rows))  # 插入倒排 = 最新在前(生产按 created_at 降序)
        return [await self._payload(ticket) for ticket in ordered]

    async def close_ticket(self, ticket_id: int) -> dict | None:
        ticket = next((row for row in self.rows if row.id == ticket_id), None)
        if ticket is None:
            return None
        if ticket.status is TicketStatus.OPEN:
            ticket.status = TicketStatus.CLOSED
            ticket.resolved_at = datetime.now(UTC)
        return await self._payload(ticket)

    async def _payload(self, ticket: Ticket) -> dict:
        name = None if ticket.customer_id is None else await self._customers.find_name(ticket.customer_id)
        return {
            "ticketId": ticket.id,
            "message": ticket.message,
            "status": ticket.status.value,
            "customerName": name,
            "createdAt": ticket.created_at.isoformat() if ticket.created_at else None,
            "resolvedAt": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        }


def _status_value(status) -> str:
    """枚举或裸字符串取小写 value(替身同时接受两种输入,与生产列口径一致)。"""
    return str(getattr(status, "value", status))


class InMemoryReportStore:
    """报表聚合内存实现(issue #21 接缝;快照端点离线可跑,不触 PG)。

    复现生产聚合口径:按状态分组计数、成交额 = 窗内 Σ(total_amount x fx_rate) 经 Decimal 量化 2 位、
    缺汇率单计 unconverted、低库存 = stock < alert_threshold(同谓词,触线不告警)、未结工单计数。
    简化披露:窗过滤按本替身收集的 Python 时间戳(生产为 SQL 表达式),排序/类型转换等 SQL 侧
    行为不复刻——聚合正确性的证明仍以集成测试(test_reports.py)为准绳,本替身只保证端点可离线跑。
    """

    def __init__(self) -> None:
        self.products: list[Product] = []
        self.orders: list[Order] = []
        self.tickets: list[Ticket] = []

    async def build_summary(self) -> dict:
        now = datetime.now(UTC)
        by_status: dict[str, int] = {}
        revenue_sum = Decimal("0")
        unconverted = 0
        for order in self.orders:
            status = _status_value(order.status)
            by_status[status] = by_status.get(status, 0) + 1
            if (order.created_at or now) >= now - timedelta(days=REVENUE_WINDOW_DAYS):
                if order.fx_rate is None:
                    unconverted += 1
                else:
                    revenue_sum += Decimal(order.total_amount) * Decimal(order.fx_rate)
        low_stock = sorted(
            (product for product in self.products if product.stock < product.alert_threshold),
            key=lambda product: product.stock,
        )
        return {
            "orders": {"byStatus": by_status, "total": sum(by_status.values())},
            "revenue": {
                "windowDays": REVENUE_WINDOW_DAYS,
                "baseCurrency": "CNY",
                "amount": str(revenue_sum.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "unconverted": unconverted,
            },
            "lowStock": [
                {
                    "productId": product.id,
                    "sku": product.sku,
                    "title": product.title,
                    "stock": product.stock,
                    "alertThreshold": product.alert_threshold,
                }
                for product in low_stock
            ],
            "tickets": {"open": sum(1 for ticket in self.tickets if _status_value(ticket.status) == "open")},
        }


class InMemoryTaskStore:
    """任务行存储内存实现(issue #13 接缝;端点流程用例离线可跑,不触 PG)。

    复现生产可见语义:未认证(user_id None)create 跳过、update 缺行静默跳过、
    列表按创建时间倒序且支持 session_id 过滤、task_session 反查 (session_id, user_id)。
    created_at 由本实现填充(PG 侧为 server_default);顺序按插入倒排,确定且等价「最新在前」。
    """

    def __init__(self) -> None:
        self._by_thread: dict[str, Task] = {}

    async def create_task_row(
        self, *, thread_id: str, user_id: int | None, session_id: str, type_: str, request: str
    ) -> None:
        if user_id is None:
            return
        self._by_thread[thread_id] = Task(
            thread_id=thread_id,
            user_id=user_id,
            session_id=session_id,
            type=type_,
            status=TaskStatus.IN_PROGRESS,
            input={"request": request},
            created_at=datetime.now(UTC),
        )

    async def update_task_row(
        self, *, thread_id: str, status: str, slice_plan: dict | None = None, result: dict | None = None
    ) -> None:
        row = self._by_thread.get(thread_id)
        if row is None:
            return
        row.status = TaskStatus(status)
        if slice_plan is not None:
            row.slice_plan = slice_plan
        if result is not None:
            row.result = result

    async def task_session(self, thread_id: str) -> tuple[str, int] | None:
        row = self._by_thread.get(thread_id)
        return (row.session_id, row.user_id) if row is not None else None

    async def list_tasks(self, *, session_id: str | None = None) -> list[Task]:
        rows = list(reversed(self._by_thread.values()))  # 插入倒排 = 最新在前(生产按 created_at 降序)
        return [row for row in rows if not session_id or row.session_id == session_id]

    async def get_task(self, thread_id: str) -> Task | None:
        return self._by_thread.get(thread_id)


def _read_envelope(row: dict) -> dict:
    """落库行 → 契约信封五键(生产的 list_for_user 同形:不泄漏 user_id/read_at 等内部列)。"""
    return {
        "notificationId": row["notificationId"],
        "message": row["message"],
        "kind": row["kind"],
        "orderId": row["orderId"],
        "timestamp": row["timestamp"],
    }


class InMemoryNotificationStore:
    """通知存储内存实现(增量 8 接缝;端点流程用例离线可跑,不触 PG)。

    复现生产可见语义:信封批量落库、按 user_ids 扇出(模拟「全量现有用户」)、
    (user_id, notificationId) 重复落库幂等(生产侧为唯一约束 + on_conflict);
    读路径(增量 8-T2):按用户隔离、每组最近 MAX_PER_KIND 条(保留最新)、
    未读 = read_at 空计数、mark_read 幂等。created_at 由本实现填充(PG 侧 server_default)。
    """

    def __init__(self, user_ids: Iterable[int] = (1,)) -> None:
        self.user_ids = list(user_ids)
        self.rows: list[dict] = []

    async def record(self, notifications: list[dict]) -> None:
        seen = {(row["user_id"], row["notificationId"]) for row in self.rows}
        for payload in notifications:
            for user_id in self.user_ids:
                if (user_id, payload["notificationId"]) in seen:
                    continue
                seen.add((user_id, payload["notificationId"]))
                self.rows.append({**payload, "user_id": user_id, "read_at": None})

    async def list_for_user(self, user_id: int) -> list[dict]:
        """信封列表:每组最近 MAX_PER_KIND 条(截断保留最新),整体最新在前。

        排列口径与生产同构:插入序倒排 ≈ created_at + id 倒排(同批 created_at 相同,id 决胜)。
        """
        indexed = [(index, row) for index, row in enumerate(self.rows) if row["user_id"] == user_id]
        by_kind: dict[str, list[tuple[int, dict]]] = {}
        for index, row in indexed:
            by_kind.setdefault(row["kind"], []).append((index, row))
        kept: list[tuple[int, dict]] = []
        for items in by_kind.values():
            kept.extend(items[-MAX_PER_KIND:])
        kept.sort(key=lambda pair: pair[0], reverse=True)
        return [_read_envelope(row) for _index, row in kept]

    async def unread_count(self, user_id: int) -> int:
        return sum(1 for row in self.rows if row["user_id"] == user_id and row["read_at"] is None)

    async def mark_read(self, user_id: int) -> None:
        """该用户全部未读置 read_at(幂等:重复调用无副作用)。"""
        now = datetime.now(UTC)
        for row in self.rows:
            if row["user_id"] == user_id and row["read_at"] is None:
                row["read_at"] = now


class FailingNotificationStore:
    """落库/读路径必炸的通知存储(增量 8):辅助簿记分类用例——仅日志,不阻塞广播、不影响响应。"""

    async def record(self, notifications: list[dict]) -> None:
        raise RuntimeError("落库炸了(测试)")

    async def list_for_user(self, user_id: int) -> list[dict]:
        raise RuntimeError("读路径炸了(测试)")

    async def unread_count(self, user_id: int) -> int:
        raise RuntimeError("读路径炸了(测试)")

    async def mark_read(self, user_id: int) -> None:
        raise RuntimeError("读路径炸了(测试)")


class InMemoryApprovalBatchStore:
    """批次存储内存实现(图级单测共享接缝;PG 实现见 db/approval_store)。

    幂等语义与生产实现一致:create 幂等、decide 同决定幂等返回、冲突抛错。
    """

    def __init__(self) -> None:
        self.batches: list[ApprovalBatchRecord] = []
        self._by_id: dict[str, ApprovalBatchRecord] = {}

    async def create_batch(
        self,
        *,
        batch_id: str,
        thread_id: str,
        slice_no: int,
        action_type: str,
        actions: list[dict],
        mode: str,
        run_output: dict | None = None,
    ) -> ApprovalBatchRecord:
        existing = self._by_id.get(batch_id)
        if existing is not None:
            return existing  # 幂等:durable 重放会重复调用
        record = ApprovalBatchRecord(
            batch_id=batch_id,
            thread_id=thread_id,
            slice_no=slice_no,
            action_type=action_type,
            actions=actions,
            status=initial_status(mode),
            mode=mode,
            run_output=run_output,
        )
        self.batches.append(record)
        self._by_id[batch_id] = record
        return record

    async def decide_batch(self, *, batch_id: str, decision: str, comment: str | None = None) -> None:
        record = self._by_id[batch_id]
        new_status = decided_status(decision)
        if record.status != "pending":
            if record.status == new_status:
                return  # 同决定幂等返回(durable 重放)
            raise BatchAlreadyDecidedError(f"批次 {batch_id} 已决定({record.status})")
        record.status = new_status
        record.comment = comment

    async def list_pending(self, thread_id: str) -> list[ApprovalBatchRecord]:
        return [r for r in self._by_id.values() if r.thread_id == thread_id and r.status == "pending"]

    async def list_by_slice(self, thread_id: str, slice_no: int) -> list[ApprovalBatchRecord]:
        return [r for r in self._by_id.values() if r.thread_id == thread_id and r.slice_no == slice_no]

    async def get_batch(self, *, batch_id: str) -> ApprovalBatchRecord | None:
        return self._by_id.get(batch_id)

    async def list_open(self) -> list[ApprovalBatchRecord]:
        return [r for r in self._by_id.values() if r.status in ("pending", "shadow")]

    async def list_by_thread(self, thread_id: str) -> list[ApprovalBatchRecord]:
        return [r for r in self._by_id.values() if r.thread_id == thread_id]


class StubPlanner:
    """规划器桩:固定计划(默认单审批切片),捕获每次规划收到的请求文本与上下文。"""

    def __init__(self, plan: SlicePlan | None = None) -> None:
        self._plan = plan or SlicePlan(
            slices=[Slice(no=1, agent="order_management", description="上架商品", approval_points=["上架审批"])]
        )
        self.requests: list[str] = []
        self.contexts: list[str | None] = []

    async def plan(self, request: str, context: str | None = None) -> SlicePlan:
        self.requests.append(request)
        self.contexts.append(context)
        return self._plan


def slice_agent(
    executed: list[int] | None = None,
    *,
    actions: list[dict] | None = None,
    answer: str | None = None,
) -> Callable[[Slice], Awaitable[dict]]:
    """切片执行桩(spec #7):记录执行序号,返回占位结果;可脚本化收集的审批动作。"""
    executed = executed if executed is not None else []

    async def run(slice_: Slice) -> dict:
        executed.append(slice_.no)
        result: dict = {"agent": slice_.agent, "description": slice_.description, "executed": True}
        if actions is not None:
            result["actions"] = actions
        if answer is not None:
            result["answer"] = answer
        return result

    return run


class FakeApply:
    """假 apply(spec #7 接缝):记录 (batch_id, actions),按需返回冲突结果。"""

    def __init__(self, *, outcomes: dict[str, ApplyResult] | None = None) -> None:
        self._outcomes = outcomes or {}
        self.calls: list[tuple[str, list[dict]]] = []

    async def __call__(self, batch_id: str, actions: list[dict]) -> ApplyResult:
        self.calls.append((batch_id, actions))
        return self._outcomes.get(batch_id, ApplyResult(applied=True))


class RecordingEmitter:
    """记录事件发射器(spec #7 接缝):按序收集 (event, payload)。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))

    def names(self) -> list[str]:
        return [event for event, _payload in self.events]


class RecordingTaskTracer:
    """记录观测调用(spec #7 接缝):trace/span 顺序与事件载荷,不依赖 Langfuse SDK。"""

    def __init__(self) -> None:
        self.traces: list[str] = []
        self.spans: list[tuple[str, dict | None]] = []
        self.events: list[tuple[str, dict]] = []

    def trace(self, thread_id: str):
        from contextlib import nullcontext

        self.traces.append(thread_id)
        return nullcontext()

    def span(self, name: str, input: dict | None = None):
        from contextlib import nullcontext

        self.spans.append((name, input))
        return nullcontext()

    def record_event(self, name: str, output: dict) -> None:
        self.events.append((name, output))


class RecordingAudit:
    """记录审计调用(图/REST 共享接缝):captured 按序收集 record 参数。"""

    def __init__(self) -> None:
        self.captured: list[dict] = []

    async def record(self, *, thread_id: str, agent_id: str, type_: str, status: str, input=None, output=None) -> None:
        self.captured.append(
            {
                "thread_id": thread_id,
                "agent_id": agent_id,
                "type": type_,
                "status": status,
                "input": input,
                "output": output,
            }
        )


# —— 增量 4(spec #7):子图与执行器共享夹具 ——


class FakeLlm:
    """按序列返回 complete / 工具轮结果;记录调用轨迹。

    complete_with_tools 按调用序返回脚本化工具轮(ToolCallResult);
    complete 按序返回文本,未配置响应时显式报错(便于测试定位意外调用)。
    """

    def __init__(
        self,
        responses: list[str | Exception] | None = None,
        tool_rounds: list[ToolCallResult | Exception] | None = None,
    ) -> None:
        self._responses = responses or []
        self._tool_rounds = tool_rounds or []
        self.calls: list[dict] = []

    async def complete(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        json_mode: bool = False,
    ) -> str:
        self.calls.append({"method": "complete", "messages": messages, "json_mode": json_mode})
        if not self._responses:
            raise AssertionError("FakeLlm.complete 未配置响应")
        response = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> ToolCallResult:
        index = len([c for c in self.calls if c["method"] == "complete_with_tools"])
        self.calls.append({"method": "complete_with_tools", "messages": messages, "tools": tools})
        if not self._tool_rounds:
            return ToolCallResult(content="", tool_calls=[])
        response = self._tool_rounds[min(index, len(self._tool_rounds) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


class FakeEmbedding:
    """确定性向量(仅测接线,不测语义):按文本长度生成同值向量。"""

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float((len(text) % 7) + 1)] * self._dim for text in texts]


class InMemoryVectorRepository(VectorRepository):
    """向量仓库内存实现(契约参考实现同款语义):余弦相似度排序,filter 最简形状。"""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, VectorRecord]] = {}

    async def upsert(self, collection: str, records: list[VectorRecord]) -> None:
        self._store.setdefault(collection, {})
        for record in records:
            self._store[collection][record.id] = record

    async def search(
        self, collection: str, vector: list[float], *, top_k: int, filter: str | None = None
    ) -> list[SearchHit]:
        hits = []
        for record in self._store.get(collection, {}).values():
            if filter and not _matches(record, filter):
                continue
            hits.append(SearchHit(id=record.id, score=_cosine(vector, record.vector), payload=record.payload))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]

    async def delete(self, collection: str, ids: list[str]) -> None:
        for id_ in ids:
            self._store.get(collection, {}).pop(id_, None)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def _matches(record: VectorRecord, filter: str) -> bool:
    field, value = filter.split(" == ")
    return record.payload.get(field) == value.strip('"')


class FakeExecutor:
    """假执行器:execute 记录并返回脚本结果;capture 记录并返回脚本快照。"""

    def __init__(
        self,
        results: dict[str, dict] | None = None,
        snapshots: dict[str, dict] | None = None,
    ) -> None:
        self._results = results or {}
        self._snapshots = snapshots or {}
        self.executed: list[tuple[str, dict]] = []
        self.captured: list[tuple[str, dict]] = []

    async def execute(self, action: str, params: dict) -> dict:
        self.executed.append((action, params))
        return self._results.get(action, {"ok": True})

    async def capture(self, action: str, params: dict) -> dict:
        self.captured.append((action, params))
        return self._snapshots.get(action, {"exists": True, "status": "draft"})
