"""``run`` 子命令的编排(spec #55 B / 票 #58;客服草稿两线 #60):哨兵校验 → 合成数据播种 →
逐条串行驱动 → 快照 + 投影。

黑盒走 REST(评的是**产品面产出**,不是图内部),**按 surface 分派两条产出线**:任务线
``POST /api/tasks``(同步语义,图跑完才响应)→ ``GET /api/tasks/{thread_id}`` 取切片级产出**与规划段**
(plan 随快照落盘,票 #61 的规划切片场景评的就是它);工作台线 ``POST /api/drafting``(同步、无任务轨迹)
→ 草稿 + 引用载荷即产出,**跑批器自建评测根 trace**(``eval:<场景id>``,分数挂它)。两线落**同一份快照
schema**,回评面零分叉。**串行**执行(仓内 LLM 并发闸 = 2,ADR-0008);金标场景一律免审(dev 剖面影子段
直行),遇 ``interrupted`` / 失败**显式报错**——不自动批准(不把机器决定混进评测语义;场景挂起 = 场景
设计缺陷,该改场景而不是让机器替人拍板)。

**净库哨兵**:``reset-db`` 在净库插一条固定 SKU 的商品,跑批前校验它必须在场——防「忘了把 app
切到净库」:那会把播种与跑批写进演示库(reset-db 与 run 之间隔着一次 app 重启,人是最不可靠的一环)。

全部 I/O 经注入的 ``httpx.AsyncClient``(测试用 ``httpx.MockTransport`` 假传输,不触真网);
投影经注入的 ``LangfuseProjection``(假 client 记录调用)。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from python_backend.evals.corpus_anchor import CorpusAnchor
from python_backend.evals.projection import Projection
from python_backend.evals.schema import WORKBENCH_SURFACE, Scenario
from python_backend.evals.snapshot import (
    SliceOutput,
    Snapshot,
    slices_from_plan,
    slices_from_results,
    snapshot_path,
    write_snapshot,
)
from python_backend.infrastructure.tracing import eval_root_trace_id, eval_root_trace_name, task_trace_id

# 净库哨兵商品 SKU(reset-db 写入、run 校验;固定值,勿改——两处共用同一常量)
SENTINEL_SKU = "EVAL-SENTINEL"
# 同步端点要等图跑完才响应:一次真跑选品 ≈2-3 分钟,超时留足余量(先例 = simulator 的 120s)
CLIENT_TIMEOUT = 900.0

# 工作台线快照的单切片落点(该线一次 = 一条草稿;切片号是标识不是下标,恒 1)
WORKBENCH_SLICE_NO = 1


def _is_blank(text: object) -> bool:
    """产出为空(去空白后为空)——跑批侧的**空产出口径**,两处检查共用。

    与 ``agents/base.answer_is_blank`` 是同一条口径(那条管作答轮护栏、这条管跑批闸):跑批器是
    黑盒 REST 客户端,不 import 业务包内件,故各自持有——**两处改一处必改另一处**。
    """
    return not str(text or "").strip()

# 合成数据播种步骤(顺序硬约束,照 OPERATIONS「试运行数据 provisioning」:商品 → 买家 → 订单)
SEED_STEPS: tuple[tuple[str, str], ...] = (
    ("/api/import/products", "synth-products.csv"),
    ("/api/import/customers", "synth-customers.csv"),
    ("/api/import/orders", "synth-orders.csv"),
)


class EvalRunner:
    """一次跑批(一个 run 名 = 一个快照目录 = 一个 Langfuse dataset run)。"""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        projection: Projection,
        snapshot_dir: Path,
        run_name: str,
        anchor: CorpusAnchor,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._projection = projection
        self._run_dir = snapshot_dir / run_name
        self._run_name = run_name
        self._anchor = anchor
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def run_dir(self) -> Path:
        """本次 run 的快照目录(``<快照根>/<run 名>``)。"""
        return self._run_dir

    def snapshot_path(self, scenario_id: str) -> Path:
        """某条场景的快照落点(目录 + 文件命名规则的唯一出口,调用方不再自己拼)。"""
        return snapshot_path(self._run_dir, scenario_id)

    async def login(self, username: str, password: str) -> None:
        """登录拿 JWT 并挂上后续请求(与 simulator 同一入口)。"""
        response = await self._client.post("/api/auth/login", json={"username": username, "password": password})
        if response.status_code != 200:
            raise RuntimeError(f"登录失败({response.status_code}):{response.text[:200]}")
        self._client.headers["Authorization"] = f"Bearer {response.json()['token']}"

    async def check_clean_db(self) -> None:
        """哨兵校验:目标 app 必须连在评测净库上(哨兵缺席 = 指着演示库 / 没跑过 reset-db)。"""
        products = (await self._get_json("/api/products")).get("products") or []
        if not any(str(product.get("sku")) == SENTINEL_SKU for product in products):
            raise RuntimeError(
                f"净库哨兵 {SENTINEL_SKU} 不在目标 app 的库里——先 `evals.py reset-db`,"
                "再按它打印的命令把 app 切到净库(跑批已中止,未写任何数据)"
            )

    async def seed_synth_data(self, csv_dir: Path) -> list[dict]:
        """确定性合成数据播种(已入仓 CSV;幂等——重跑全 skipped);返回三份导入报告。"""
        reports: list[dict] = []
        for endpoint, name in SEED_STEPS:
            source = csv_dir / name
            if not source.exists():
                raise RuntimeError(f"合成数据缺失:{source}(先跑 `scripts/gen_synth_data.py` 生成)")
            response = await self._client.post(endpoint, content=source.read_text(encoding="utf-8").encode())
            if response.status_code != 200:
                raise RuntimeError(f"播种失败 {name}({response.status_code}):{response.text[:200]}")
            reports.append(response.json()["report"])
        return reports

    async def run_scenarios(
        self, scenarios: list[Scenario], *, on_scenario: Callable[[Snapshot], None] | None = None
    ) -> list[Snapshot]:
        """场景集 → dataset(dataset 先就位,run item 才有落点)→ 逐条串行驱动;一条失败即中止。

        ``on_scenario`` 只作进度回显(一条真跑 ≈2-3 分钟,CLI 要能报出刚跑完哪条),不参与编排。
        """
        self._projection.sync_scenarios(scenarios)
        snapshots: list[Snapshot] = []
        for scenario in scenarios:
            snapshot = await self._run_one(scenario)
            snapshots.append(snapshot)
            if on_scenario is not None:
                on_scenario(snapshot)
        return snapshots

    async def _run_one(self, scenario: Scenario) -> Snapshot:
        """一条场景 → 快照:按 surface 分派产出线(工作台线走同步端点,其余走任务线)。"""
        if scenario.surface == WORKBENCH_SURFACE:
            return await self._run_workbench(scenario)
        return await self._run_task(scenario)

    async def _run_task(self, scenario: Scenario) -> Snapshot:
        """任务线一条场景:触发任务 → 校验终态 → 读切片产出与规划段 → 落快照 → 投影 dataset run item。

        规划段(``plan``)随快照一起落盘(票 #61):规划切片场景评的就是它——**同一次任务跑批**的规划
        产物,不另设「只规划」捷径。
        """
        response = await self._client.post(
            "/api/tasks", json={"request": scenario.input, "session_id": f"eval-{self._run_name}"}
        )
        if response.status_code != 201:
            raise RuntimeError(f"场景 {scenario.id} 触发任务失败({response.status_code}):{response.text[:200]}")
        created = response.json()
        thread_id = str(created["threadId"])
        status = str(created.get("status"))
        if status == "interrupted":
            raise RuntimeError(
                f"场景 {scenario.id} 挂起等审批(interrupt)——金标场景一律免审,"
                "这是场景设计缺陷(不自动批准):改场景输入或检查剖面"
            )
        if status != "completed":
            raise RuntimeError(
                f"场景 {scenario.id} 任务未完成({status}):{created.get('error') or created.get('summary')}"
            )

        detail = await self._get_json(f"/api/tasks/{thread_id}")
        slices = slices_from_results(detail.get("results"))
        if not slices:
            raise RuntimeError(f"场景 {scenario.id} 无切片产出(任务详情的 results 为空)——无可评对象")
        # #64 A4:空产出要在**跑批这里**就暴露。任务行报 completed 却给出无答案的切片,正是
        # outdoor-trend 的实录(answer: null 静默通过,直到 judge 判「产出为空」才发现);未完成
        # 是如实产出(带原因),不算空产出——两者靠 incomplete 分开。
        blank = [item.no for item in slices if _is_blank(item.answer) and not item.incomplete]
        if blank:
            raise RuntimeError(
                f"场景 {scenario.id} 切片 {blank} 无产出且未标未完成——空产出与未完成须可辨,"
                "跑批中止(不落一份看着正常的空快照)"
            )
        snapshot = Snapshot(
            scenario_id=scenario.id,
            surface=scenario.surface,
            run_name=self._run_name,
            thread_id=thread_id,
            trace_id=task_trace_id(thread_id),
            status=str(detail.get("status") or status),
            slices=slices,
            # 规划段随任务线快照一起落盘(票 #61):规划切片场景评的就是它,别的线不消费、如实带着
            plan=slices_from_plan(detail.get("plan")),
            corpus_fingerprint=self._anchor.fingerprint,
            corpus_batch_id=self._anchor.batch_id,
            dataset_run_id=None,
            recorded_at=self._now(),
        )
        # 先落盘再投影:任务已烧真 token,投影失败(网络/Langfuse 抖动)也不该丢产出
        return await self._write_and_project(snapshot)

    async def _run_workbench(self, scenario: Scenario) -> Snapshot:
        """工作台线一条场景:同步端点出草稿 → 单切片快照 → **自建评测根 trace** → 投影 run item。

        该线没有任务轨迹:快照 trace_id 走 ``eval_root_trace_id``(与建出的根 trace 同一派生,
        两处恒等),``thread_id`` 留空(如实:无任务线程)。产出落盘先于投影,同任务线一条口径。
        """
        response = await self._client.post("/api/drafting", json={"message": scenario.input, "locale": scenario.locale})
        if response.status_code != 200:
            raise RuntimeError(f"场景 {scenario.id} 起草失败({response.status_code}):{response.text[:200]}")
        payload = response.json()
        draft = str(payload.get("draft") or "")
        if _is_blank(draft):
            raise RuntimeError(f"场景 {scenario.id} 草稿产出为空——无可评对象(端点返回了空草稿)")
        trace_id = eval_root_trace_id(scenario.id)
        snapshot = Snapshot(
            scenario_id=scenario.id,
            surface=scenario.surface,
            run_name=self._run_name,
            thread_id="",
            trace_id=trace_id,
            status="completed",
            slices=(
                SliceOutput(
                    no=WORKBENCH_SLICE_NO,
                    agent="drafting",
                    description="起草工作台:查证(FAQ/订单/商品)→ 多语草稿",
                    answer=draft,
                    citations=tuple(payload.get("citations") or ()),
                    executed=True,
                ),
            ),
            plan=(),  # 该线无任务轨迹 ⇒ 无规划段(如实留空,不编一个)
            corpus_fingerprint=self._anchor.fingerprint,
            corpus_batch_id=self._anchor.batch_id,
            dataset_run_id=None,
            recorded_at=self._now(),
        )
        return await self._write_and_project(
            snapshot,
            create_trace=lambda: self._projection.create_eval_trace(
                trace_id=trace_id,
                name=eval_root_trace_name(scenario.id),
                input=scenario.input,
                output=draft,
            ),
        )

    async def _write_and_project(
        self, snapshot: Snapshot, *, create_trace: Callable[[], None] | None = None
    ) -> Snapshot:
        """落盘 → (可选)自建评测根 trace → 投影 dataset run item → 回填 dataset run id。

        **先落盘再投影**:产出已烧真 token,投影失败(Langfuse 抖动 / 不可达)也不该丢产出——
        报错里点明快照无恙。``create_trace`` 是工作台线的评测根 trace 步骤(该线无任务轨迹,
        分数得挂自建的 trace);任务线的 trace 由 app 容器在跑任务时建好,不走此步。
        metadata 取快照自身字段(单一来源:落盘的那份即投影的那份),两线同形。
        """
        path = write_snapshot(self._run_dir, snapshot)
        try:
            if create_trace is not None:
                create_trace()
            dataset_run_id = self._projection.record_run(
                run_name=self._run_name,
                scenario_id=snapshot.scenario_id,
                trace_id=snapshot.trace_id,
                metadata={
                    "surface": snapshot.surface,
                    "corpus_fingerprint": snapshot.corpus_fingerprint,
                    "corpus_batch_id": snapshot.corpus_batch_id,
                    "slice_count": len(snapshot.slices),
                },
            )
        except Exception as error:  # SDK 异常(Langfuse 5xx / 不可达):转成编排层的显式报错,并点明快照无恙
            raise RuntimeError(f"场景 {snapshot.scenario_id} 投影失败(产出快照已落盘:{path}):{error}") from error
        if dataset_run_id is None:
            return snapshot
        snapshot = replace(snapshot, dataset_run_id=dataset_run_id)
        write_snapshot(self._run_dir, snapshot)  # 回填 dataset run id(#59 的分数据此互链)
        return snapshot

    async def _get_json(self, path: str) -> dict:
        response = await self._client.get(path)
        if response.status_code != 200:
            raise RuntimeError(f"GET {path} 失败({response.status_code}):{response.text[:200]}")
        return response.json()
