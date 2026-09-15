"""评测跑批 CLI(spec #55 B):金标场景真源 ↔ 真实任务产出 ↔ Langfuse 判据分数。

子命令(与 scripts/ingest_corpus.py 同构:模块 docstring 写用法 + argparse + main()):

- **list**:列出评测真源里的场景(id / surface / 判据条数)
- **validate**:校验评测真源(形状非法即报错)——跑批前的自检
- **reset-db**:重建评测净库(dropdb/createdb + 迁移 + 哨兵)——跑批与演示素材物理隔离
- **run**:驱动真实任务 → 产出快照 → Langfuse 投影(票 #58 落地)
- **score**:从已存快照回评(机械防伪引 + judge)→ 分数落 Langfuse(票 #59 落地;不重跑任务)

用法(在 python-backend/ 下)::

    uv run python scripts/evals.py list [--scenarios docs/evals/product-report.yaml]
    uv run python scripts/evals.py validate

跑批工作流(三步;净库与演示素材物理隔离,ADR-0008)::

    # 1. 重建评测净库(默认 mae_eval;演示库与旧系统冻结库一律拒绝)
    uv run python scripts/evals.py reset-db

    # 2. 按 reset-db 打印的命令把 app 切到净库(仓库根执行;仅跑批期间)
    APP_DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/mae_eval docker compose up -d app

    # 3. 跑批:哨兵校验 → 合成数据播种(幂等)→ 逐条串行驱动 → 快照 + Langfuse 投影
    uv run python scripts/evals.py run --base-url http://localhost:3000

    # 跑完切回演示库:docker compose up -d app

回评(不重跑任务,票 #59)::

    uv run python scripts/evals.py score [--run <run 名|路径>]   # 缺省取最近一次 run

``score`` 只读快照回评(rubric 从真源现读 → **改 rubric / 换 judge 只重跑本命令**;run 烧真
token,score 只烧 judge token):机械防伪引(零 LLM)+ judge 逐条 0/1 + comment,分数落 Langfuse
(带 trace_id / dataset_run_id / 语料指纹),写完读回核实。``--scenarios`` 可指另一份 rubric 真源。

真源目录 = ``docs/evals/*.yaml``(默认全量,可显式传文件);产出快照落 ``docs/evals/runs/<run 名>/``
(gitignore)。``run`` / ``score`` 烧真 token、要真服务、走专用净库——与摄入 CLI 同待遇:不进快速套件
(核心逻辑离线替身测于 ``python_backend.evals`` 包)。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
from sqlalchemy.engine import URL, make_url

from python_backend.evals.corpus_anchor import CorpusAnchor, corpus_anchor
from python_backend.evals.judge import AnthropicJudge, load_judge_config
from python_backend.evals.projection import DATASET_NAME, build_langfuse_client, build_projection, load_langfuse_config
from python_backend.evals.runner import CLIENT_TIMEOUT, SENTINEL_SKU, EvalRunner
from python_backend.evals.schema import Scenario, load_scenarios
from python_backend.evals.scores import LangfuseScores
from python_backend.evals.scoring import (
    ScoreRecord,
    load_run_snapshots,
    score_run,
)
from python_backend.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_BACKEND_DIR = Path(__file__).resolve().parents[1]
EVALS_DIR = REPO_ROOT / "docs" / "evals"
CORPUS_DIR = REPO_ROOT / "docs" / "corpus"
DEMO_DATA_DIR = REPO_ROOT / "docs" / "demo-data"

# 本票只实现「选品报告」面(客服草稿 / 规划切片面由票 #60 铺开);其余面场景显式跳过并打印
IMPLEMENTED_SURFACES = ("选品报告",)

# 评测净库默认名(与验证库 mae_verify、演示库 mae 各不相干)
DEFAULT_EVAL_DB = "mae_eval"
# 拒绝重建的库:演示库有素材、旧系统库是冻结资产(与 OPERATIONS 清库 runbook 同款禁令)
PROTECTED_DBS = ("mae", "multi_agent_ecommerce")
# 容器侧 DB 主机/端口(镜像 docker-compose.yml 里 app 服务的约定;reset-db 打印切换命令用)
COMPOSE_DB_HOST = "postgres"
COMPOSE_DB_PORT = 5432

# 净库哨兵行:run 开跑前校验它必须在场(含义见 evals/runner.py 的哨兵说明)
SENTINEL_SQL = """
INSERT INTO products
  (sku, title, description, price, currency, platform, category, status, stock, alert_threshold)
VALUES
  (%s, '评测净库哨兵(勿删)', 'reset-db 写入、run 校验;删掉会让跑批拒绝启动',
   0, 'CNY', 'eval', '评测哨兵', 'draft', 0, 0)
ON CONFLICT (sku) DO NOTHING
"""


def list_scenarios(args: argparse.Namespace) -> None:
    scenarios = _load(args)
    print("id\tsurface\t判据")
    for scenario in scenarios:
        print(f"{scenario.id}\t{scenario.surface}\t{len(scenario.rubric)} 条")
    print(f"共 {len(scenarios)} 条场景")


def validate(args: argparse.Namespace) -> None:
    scenarios = _load(args)
    counts = Counter(scenario.surface for scenario in scenarios)
    print(f"评测真源合法:{len(scenarios)} 条场景")
    for surface, count in counts.items():
        print(f"  {surface}: {count} 条")


def reset_db(args: argparse.Namespace) -> None:
    """重建评测净库:dropdb/createdb(维护连接)→ 迁移(子进程)→ 哨兵行 → 打印切换命令。"""
    target = args.db
    _check_reset_target(target)
    settings = get_settings()
    server = make_url(settings.database_url)
    print(f"重建净库 {target}(server {server.host}:{server.port})——演示库不参与,也不受影响")
    with psycopg.connect(_dsn(server.set(database="postgres")), autocommit=True) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)')
        connection.execute(f'CREATE DATABASE "{target}"')

    print("迁移(alembic upgrade head)…")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PYTHON_BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": _url_with_password(server.set(database=target))},
        check=True,
    )
    print(f"哨兵行 {SENTINEL_SKU} …")
    with psycopg.connect(_dsn(server.set(database=target)), autocommit=True) as connection:
        connection.execute(SENTINEL_SQL, (SENTINEL_SKU,))

    compose_url = _url_with_password(server.set(database=target, host=COMPOSE_DB_HOST, port=COMPOSE_DB_PORT))
    print(
        "\n净库就绪。下一步(两步,缺一不可):\n"
        f"  1) 仓库根执行:APP_DATABASE_URL={compose_url} docker compose up -d app\n"
        "  2) 跑批:uv run python scripts/evals.py run --base-url http://localhost:3000\n"
        "跑完切回演示库:docker compose up -d app"
    )


def run(args: argparse.Namespace) -> None:
    try:
        asyncio.run(_run(args))
    except RuntimeError as error:  # 编排各处的显式报错(登录/哨兵/中断/失败/投影)——照实转达并中止
        raise SystemExit(f"跑批中止:{error}") from error


async def _run(args: argparse.Namespace) -> None:
    scenarios = _load(args)
    runnable = [scenario for scenario in scenarios if scenario.surface in IMPLEMENTED_SURFACES]
    for skipped in (scenario for scenario in scenarios if scenario.surface not in IMPLEMENTED_SURFACES):
        print(f"跳过 {skipped.id}({skipped.surface}):本票只实现「选品报告」面(票 #60 铺开)")
    if not runnable:
        raise SystemExit("没有「选品报告」面场景——先补 docs/evals/*.yaml")

    settings = get_settings()
    projection = build_projection(settings)  # 缺密钥即显式报错(不静默降级为「跑完但没落库」)
    run_name = args.run_name or f"run-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    anchor = await _anchor()
    print(f"run 名 {run_name} / 语料指纹 {anchor.fingerprint[:12]}… / 批次 {anchor.batch_id or '(留空)'}")
    print(f"Langfuse {load_langfuse_config(settings).host}(投影目标;dataset 名 {DATASET_NAME})")
    if anchor.note:
        print(f"  语料批次附记:{anchor.note}")
    print(f"场景 {len(runnable)} 条(串行,单条真跑 ≈2-3 分钟):" + " → ".join(item.id for item in runnable))

    async with httpx.AsyncClient(base_url=args.base_url, timeout=CLIENT_TIMEOUT) as client:
        runner = EvalRunner(
            client, projection=projection, snapshot_dir=EVALS_DIR / "runs", run_name=run_name, anchor=anchor
        )
        await runner.login(settings.auth_admin_username, settings.auth_admin_password)
        await runner.check_clean_db()  # 哨兵缺席即中止:播种与跑批绝不写演示库
        if not args.skip_seed:
            reports = await runner.seed_synth_data(DEMO_DATA_DIR)
            print("播种:" + " / ".join(f"新建 {item['created']} 跳过 {item['skipped']}" for item in reports))

        snapshots = await runner.run_scenarios(
            runnable,
            on_scenario=lambda snapshot: print(
                f"  完成 {snapshot.scenario_id}:thread {snapshot.thread_id} / trace {snapshot.trace_id} / "
                f"切片 {len(snapshot.slices)} / 快照 {runner.snapshot_path(snapshot.scenario_id)}"
            ),
        )
    projection.flush()  # 冲刷 SDK 缓冲,退出前把最近写入报上去
    print(f"完成:快照 {len(snapshots)} 份 → {runner.run_dir};Langfuse dataset run = {run_name}")


def score(args: argparse.Namespace) -> None:
    """从已存快照回评:机械防伪引 + judge 判据 → 分数落 Langfuse(不重跑任务,票 #59)。

    快照是唯一输入:rubric 从真源现读,故**改 rubric / 换 judge 只重跑本命令**——run 烧真 token,
    score 只烧 judge token(ADR-0008 的解耦点在此兑现)。
    """
    try:
        _score(args)
    except RuntimeError as error:  # 配置闸 / 快照 / judge 失败与坏输出——照实转达并中止
        raise SystemExit(f"回评中止:{error}") from error


def _score(args: argparse.Namespace) -> None:
    run_dir = _run_dir(args.run)
    snapshots = load_run_snapshots(run_dir)
    scenarios = _load(args)
    judge_config = load_judge_config()
    judge = AnthropicJudge(judge_config)
    config = load_langfuse_config()
    client = build_langfuse_client(config)
    sink = LangfuseScores(client)

    print(f"回评 {run_dir.name}:快照 {len(snapshots)} 份(rubric 取自评测真源,改它即换判定口径)")
    print(f"Langfuse {config.host} / judge {judge_config.model}")
    print("场景:" + " → ".join(snapshot.scenario_id for snapshot in snapshots))

    def announce(record: ScoreRecord) -> None:
        comment = record.comment if len(record.comment) <= 40 else record.comment[:40] + "…"
        print(f"  {record.name} = {record.value} | {comment}")

    result = score_run(snapshots, scenarios, judge=judge, sink=sink, on_record=announce)
    sink.flush()  # 冲掉 SDK 缓冲后读回,否则最近写入可能还没上报
    sink.verify(result.records)
    print(f"完成:分数 {len(result.records)} 条(通过 {result.passed} / 失败 {len(result.records) - result.passed})")
    print("Langfuse 3001 → 任务的 Scores 面板(带 trace_id / dataset_run_id / 语料指纹)")


def _run_dir(run: str | None) -> Path:
    """``--run`` → 快照目录:绝对路径照用,否则按 run 名解析(缺省取最近一次 run)。"""
    runs_root = EVALS_DIR / "runs"
    if run is None:
        candidates = [path for path in runs_root.glob("*") if path.is_dir()]
        if not candidates:
            raise SystemExit(f"{runs_root} 下没有 run——先跑 `evals.py run`(README 三步见模块文档)")
        return max(candidates, key=lambda path: path.stat().st_mtime)
    path = Path(run)
    if path.is_absolute():
        return path
    if (runs_root / run).is_dir():
        return runs_root / run
    return _resolve(path)


async def _anchor() -> CorpusAnchor:
    paths = sorted(CORPUS_DIR.glob("*.yaml"))
    if not paths:
        raise SystemExit(f"{CORPUS_DIR} 下没有语料真源——指纹无从算起")
    return await corpus_anchor(paths)


def _check_reset_target(target: str) -> None:
    """重建目标闸:名字须是安全的裸标识符,且不得是演示库/旧系统冻结库。"""
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", target):
        raise SystemExit(f"库名非法:{target!r}(只收小写字母/数字/下划线,须字母或下划线开头)")
    if target in PROTECTED_DBS:
        raise SystemExit(f"拒绝重建 {target}:演示库有素材、旧系统库是冻结资产(评测净库请用 {DEFAULT_EVAL_DB})")


def _url_with_password(url: URL) -> str:
    """SQLAlchemy URL 渲染,**保留密码**(要写进命令行与子进程环境,默认渲染是 ***)。"""
    return url.render_as_string(hide_password=False)


def _dsn(url: URL) -> str:
    """psycopg 直连 DSN:驱动换回无前缀的 postgresql(same 语义见 settings.postgres_dsn)。"""
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _load(args: argparse.Namespace) -> list[Scenario]:
    explicit = args.scenarios or []
    paths = [_resolve(Path(item)) for item in explicit] if explicit else sorted(EVALS_DIR.glob("*.yaml"))
    if not paths:
        raise SystemExit(f"{EVALS_DIR} 下没有评测真源——先补场景文件")
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise SystemExit(f"评测真源不存在:{'、'.join(str(path) for path in missing)}")
    try:
        return load_scenarios(paths)
    except ValueError as error:
        raise SystemExit(f"评测真源非法:{error}") from error


def _resolve(path: Path) -> Path:
    """显式路径相对**仓库根**解析(与 EVALS_DIR 同基准)——相对 CWD 在 python-backend/ 下找不到 docs/。"""
    return path if path.is_absolute() else REPO_ROOT / path


def main() -> None:
    parser = argparse.ArgumentParser(description="评测跑批:金标场景真源 → 真实任务产出 → Langfuse 判据分数")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出评测真源里的场景")
    list_parser.add_argument("--scenarios", nargs="*", help=f"场景文件(默认 {EVALS_DIR}/*.yaml)")
    list_parser.set_defaults(func=list_scenarios)

    validate_parser = subparsers.add_parser("validate", help="校验评测真源(形状非法即报错)")
    validate_parser.add_argument("--scenarios", nargs="*", help=f"场景文件(默认 {EVALS_DIR}/*.yaml)")
    validate_parser.set_defaults(func=validate)

    reset_parser = subparsers.add_parser("reset-db", help="重建评测净库(dropdb/createdb + 迁移 + 哨兵)")
    reset_parser.add_argument("--db", default=DEFAULT_EVAL_DB, help=f"净库名(默认 {DEFAULT_EVAL_DB})")
    reset_parser.set_defaults(func=reset_db)

    run_parser = subparsers.add_parser("run", help="驱动真实任务产出快照并投影 Langfuse(票 #58)")
    run_parser.add_argument("--base-url", required=True, help="评测 app 的地址(须连在净库上;reset-db 会打印)")
    run_parser.add_argument("--scenarios", nargs="*", help=f"场景文件(默认 {EVALS_DIR}/*.yaml)")
    run_parser.add_argument("--run-name", help="run 名(默认 UTC 时间戳)——dataset run 名与快照目录名")
    run_parser.add_argument("--skip-seed", action="store_true", help="跳过合成数据播种(重跑时省几秒)")
    run_parser.set_defaults(func=run)

    score_parser = subparsers.add_parser("score", help="从已存快照回评:机械防伪引 + judge → Langfuse 分数")
    score_parser.add_argument(
        "--run",
        help=f"run 名或快照目录(缺省取 {EVALS_DIR / 'runs'} 下最近一次);run 名相对该目录解析",
    )
    score_parser.add_argument("--scenarios", nargs="*", help=f"判据真源(默认 {EVALS_DIR}/*.yaml)——改它即换 rubric")
    score_parser.set_defaults(func=score)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
