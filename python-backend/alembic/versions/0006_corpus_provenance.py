"""语料溯源与批次台账(ADR-0007 / spec #46 A):迁移 0006,链尾 0005_notifications 之后。

- ``faq`` / ``market_intel``:补切块级溯源列 —— ``chunk_id`` 为自然键(``<doc_id>#<序号>``,
  确定性派生),重灌同 id 覆盖即幂等;「标题」faq 侧用既有 question,market_intel 侧新增 title。
- ``market_intel.source`` 由 ``String(40)`` 放宽至 ``String(200)``(真实报告名 / URL 装不下)。
- 新增 ``corpus_batches``:语料批次台账(批次 id / 文档标识 / 内容哈希 / 摄入时间 / 切块数)。

两表迁移前均为 0 行(全库无写入者,实测),故溯源列直接 NOT NULL——写入者只有摄入脚本。

Revision ID: 0006_corpus_provenance
"""

import sqlalchemy as sa

from alembic import op

revision = "0006_corpus_provenance"
down_revision = "0005_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("faq", sa.Column("chunk_id", sa.String(80), nullable=False))
    op.add_column("faq", sa.Column("doc_id", sa.String(48), nullable=False))
    op.add_column("faq", sa.Column("source", sa.String(200), nullable=False))
    op.add_column("faq", sa.Column("published_at", sa.Date(), nullable=False))
    op.add_column("faq", sa.Column("section", sa.String(120), nullable=False))
    op.add_column("faq", sa.Column("chunk_index", sa.Integer(), nullable=False))
    op.create_unique_constraint("uq_faq_chunk_id", "faq", ["chunk_id"])

    op.alter_column("market_intel", "source", type_=sa.String(200), existing_type=sa.String(40))
    op.add_column("market_intel", sa.Column("title", sa.String(300), nullable=False))
    op.add_column("market_intel", sa.Column("chunk_id", sa.String(80), nullable=False))
    op.add_column("market_intel", sa.Column("doc_id", sa.String(48), nullable=False))
    op.add_column("market_intel", sa.Column("published_at", sa.Date(), nullable=False))
    op.add_column("market_intel", sa.Column("section", sa.String(120), nullable=False))
    op.add_column("market_intel", sa.Column("chunk_index", sa.Integer(), nullable=False))
    op.create_unique_constraint("uq_market_intel_chunk_id", "market_intel", ["chunk_id"])

    op.create_table(
        "corpus_batches",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("doc_id", sa.String(48), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("batch_id", "doc_id"),
    )


def downgrade() -> None:
    op.drop_table("corpus_batches")
    op.drop_constraint("uq_market_intel_chunk_id", "market_intel", type_="unique")
    for column in ("chunk_index", "section", "published_at", "doc_id", "chunk_id", "title"):
        op.drop_column("market_intel", column)
    op.alter_column("market_intel", "source", type_=sa.String(40), existing_type=sa.String(200))
    op.drop_constraint("uq_faq_chunk_id", "faq", type_="unique")
    for column in ("chunk_index", "section", "published_at", "source", "doc_id", "chunk_id"):
        op.drop_column("faq", column)
