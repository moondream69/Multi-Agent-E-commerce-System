"""评测跑批 CLI(spec #55 B):金标场景真源 ↔ 真实任务产出 ↔ Langfuse 判据分数。

子命令(与 scripts/ingest_corpus.py 同构:模块 docstring 写用法 + argparse + main()):

- **list**:列出评测真源里的场景(id / surface / 判据条数)
- **validate**:校验评测真源(形状非法即报错)——跑批前的自检
- **run**:驱动真实任务 → 产出快照 → Langfuse 投影(票 #58 落地)
- **score**:从已存快照回评(机械防伪引 + judge)→ 分数落 Langfuse(票 #59 落地)

用法(在 python-backend/ 下)::

    uv run python scripts/evals.py list [--scenarios docs/evals/product-report.yaml]
    uv run python scripts/evals.py validate

真源目录 = ``docs/evals/*.yaml``(默认全量,可显式传文件)。``run`` / ``score`` 烧真 token、
要真服务、走专用净库——与摄入 CLI 同待遇:不进快速套件(核心逻辑离线替身测于评测包)。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from python_backend.evals.schema import Scenario, load_scenarios

REPO_ROOT = Path(__file__).resolve().parents[2]
EVALS_DIR = REPO_ROOT / "docs" / "evals"


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


def run(args: argparse.Namespace) -> None:
    raise SystemExit("run 未实现(票 #58 落地:净库重建 + 场景驱动 + 产出快照 + Langfuse 投影)")


def score(args: argparse.Namespace) -> None:
    raise SystemExit("score 未实现(票 #59 落地:机械防伪引 + judge 判据 → Langfuse 分数)")


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

    run_parser = subparsers.add_parser("run", help="驱动真实任务产出快照(未实现,票 #58)")
    run_parser.set_defaults(func=run)

    score_parser = subparsers.add_parser("score", help="从已存快照回评、分数落 Langfuse(未实现,票 #59)")
    score_parser.set_defaults(func=score)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
