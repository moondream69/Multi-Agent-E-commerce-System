"""语料摄入 CLI(spec #46 A):真源语料文件 ↔ 检索库。

两条子命令(与 scripts/gen_synth_data.py 同构:模块 docstring 写用法 + argparse + main()):

- **freeze**:取一份真实公开报告(PDF 直链或本地文件)→ pypdf 逐页抽取 → 冻结进语料文件(真源,YAML)
- **ingest**:读语料文件 → 切块 → 嵌入(Ollama bge-m3)→ Milvus upsert + PG 投影 + 批次台账

用法(在 python-backend/ 下)::

    uv run python scripts/ingest_corpus.py freeze --pdf <url 或本地路径> --doc-id <标识> \\
        --title "报告标题" --source "出版方(许可)" --published-at 2020-01-01 --category 行业洞察 \\
        [--pages-url <报告页链接>] [--attribution "署名/许可文本"] [--out docs/corpus/market-intel.yaml]

    uv run python scripts/ingest_corpus.py ingest [--corpus docs/corpus/market-intel.yaml ...]

幂等:切块标识确定性派生(``<doc_id>#<序号>``),重复 ingest 为覆盖;每次 ingest 记一条批次台账。
``DATABASE_URL`` 经环境变量覆盖(净库验证:指向一次性库跑完即 drop;不碰 dev 库)。

freeze 需要能取到 PDF 字节(海外直链须走代理:``HTTPS_PROXY=http://127.0.0.1:7897``);
ingest 需要 Ollama / Milvus / Postgres 在线(compose 默认栈)。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import httpx

from python_backend.corpus.chunking import chunk_document
from python_backend.corpus.pdf import extract_pages
from python_backend.corpus.pipeline import ingest_documents
from python_backend.corpus.schema import CorpusDocument, load_corpus, save_document
from python_backend.db.corpus_store import PostgresCorpusStore
from python_backend.infrastructure.embedding import EmbeddingService
from python_backend.vector_repo.milvus_repo import MilvusVectorRepository

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "docs" / "corpus"
DEFAULT_OUT = CORPUS_DIR / "market-intel.yaml"


def freeze(args: argparse.Namespace) -> None:
    data = _read_pdf_bytes(args.pdf)
    pages = extract_pages(data)
    if not pages:
        raise SystemExit(f"PDF 未抽出任何文本层:{args.pdf}(扫描件需 OCR,不在范围内)")
    out = _resolve(args.out)
    document = CorpusDocument(
        doc_id=args.doc_id,
        kind="intel",
        title=args.title,
        source=args.source,
        published_at=date.fromisoformat(args.published_at),
        category=args.category,
        pages=pages,
        url=args.pdf if args.pdf.startswith("http") else args.pages_url,
        attribution=args.attribution,
        license_note=args.license_note,
        source_sha256=hashlib.sha256(data).hexdigest(),
    )
    save_document(out, document)

    # 自检:回读真源 + 离线预演切块(不触任何服务)——坏语料当场暴露
    [stored] = [doc for doc in load_corpus([out]) if doc.doc_id == document.doc_id]
    chunks = chunk_document(stored)
    lengths = [len(chunk.content) for chunk in chunks]
    print(f"冻结 {args.doc_id} → {out}")
    print(f"  页数 {len(stored.pages)} / 字符 {sum(len(page.text) for page in stored.pages)}")
    print(f"  预演切块 {len(chunks)} 块(长度 {min(lengths)}~{max(lengths)} 字符)")


def _read_pdf_bytes(source: str) -> bytes:
    if not source.startswith("http"):
        return Path(source).read_bytes()
    try:
        response = httpx.get(source, follow_redirects=True, timeout=120.0)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise SystemExit(f"取 PDF 失败({error};海外直链需 HTTPS_PROXY=http://127.0.0.1:7897)") from error
    return response.content


def _resolve(path: Path) -> Path:
    """显式路径相对**仓库根**解析(与 DEFAULT_OUT 同基准)——相对 CWD 在 python-backend/ 下找不到 docs/。"""
    return path if path.is_absolute() else REPO_ROOT / path


async def ingest(args: argparse.Namespace) -> None:
    paths = [_resolve(Path(item)) for item in args.corpus] if args.corpus else sorted(CORPUS_DIR.glob("*.yaml"))
    if not paths:
        raise SystemExit(f"{CORPUS_DIR} 下没有语料文件——先跑 freeze")
    documents = load_corpus(paths)
    batch_id = str(uuid4())
    try:
        outcomes = await ingest_documents(
            documents,
            embedding=EmbeddingService(),
            vector=MilvusVectorRepository(),
            store=PostgresCorpusStore(),
            batch_id=batch_id,
        )
    except httpx.HTTPError as error:
        raise SystemExit(f"嵌入/写入失败({error})——检查 Ollama(bge-m3)与 Milvus 是否在线,已中止且未留半写") from error
    print(f"语料批次 {batch_id}(语料文件: {', '.join(path.name for path in paths)})")
    for outcome in outcomes:
        print(f"  {outcome.doc_id}: {outcome.chunk_count} 块 / 内容哈希 {outcome.content_hash[:12]}…")


def _run_ingest(args: argparse.Namespace) -> None:
    asyncio.run(ingest(args))


def main() -> None:
    parser = argparse.ArgumentParser(description="语料摄入:冻结真源(freeze)+ 投影进检索库(ingest)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze_parser = subparsers.add_parser("freeze", help="PDF → 语料文件(真源 YAML)")
    freeze_parser.add_argument("--pdf", required=True, help="PDF 直链或本地路径")
    freeze_parser.add_argument("--doc-id", required=True, help="稳定文档标识(≤48 字符,不含 '#')")
    freeze_parser.add_argument("--title", required=True)
    freeze_parser.add_argument("--source", required=True, help="来源渠道(出版方 + 许可)")
    freeze_parser.add_argument("--published-at", required=True, help="发布日期 YYYY-MM-DD")
    freeze_parser.add_argument("--category", required=True, help="情报四类之一")
    freeze_parser.add_argument("--pages-url", help="报告页链接(PDF 为本地文件时记这里)")
    freeze_parser.add_argument("--attribution", help="署名 / 许可文本")
    freeze_parser.add_argument("--license-note", help="条款依据一句话(复核用:为什么这份材料可收)")
    freeze_parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    freeze_parser.set_defaults(func=freeze)

    ingest_parser = subparsers.add_parser("ingest", help="语料文件 → Milvus + PG + 批次台账")
    ingest_parser.add_argument("--corpus", nargs="*", help=f"语料文件(默认 {CORPUS_DIR}/*.yaml)")
    ingest_parser.set_defaults(func=_run_ingest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
