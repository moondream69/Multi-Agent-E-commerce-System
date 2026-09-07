"""add sessions to conversations

Revision ID: e9f2a3b4c5d6
Revises: c3d8e2f4a1b6
Create Date: 2026-09-07 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "c3d8e2f4a1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema:多会话(每 (customerId, sessionId) 一行),现有单行升级为默认会话。"""
    op.add_column(
        "conversations",
        sa.Column("sessionId", sa.String(length=255), server_default="default", nullable=False),
    )
    op.add_column("conversations", sa.Column("title", sa.String(length=255), nullable=True))
    op.create_unique_constraint(
        "uq_conversations_customer_session", "conversations", ["customerId", "sessionId"]
    )

    # 数据回填:标题取现有消息里首条用户消息的前 20 字
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, messages FROM conversations")).fetchall()
    for row_id, messages in rows:
        title = None
        for entry in messages or []:
            if isinstance(entry, dict) and entry.get("role") == "user":
                title = (entry.get("content") or "")[:20]
                break
        if title:
            conn.execute(
                sa.text("UPDATE conversations SET title = :title WHERE id = :row_id"),
                {"title": title, "row_id": row_id},
            )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_conversations_customer_session", "conversations", type_="unique")
    op.drop_column("conversations", "title")
    op.drop_column("conversations", "sessionId")
