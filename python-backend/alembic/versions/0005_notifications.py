"""通知落库(增量 8-T1,spec #14):迁移 0005,notifications 表。

WS 广播之外的持久副本:按用户扇出(每用户一行),未读 = read_at 为空;
唯一约束 (user_id, notification_id) 保重放幂等。

Revision ID: 0005_notifications
"""

import sqlalchemy as sa

from alembic import op

revision = "0005_notifications"
down_revision = "0004_product_alert_threshold"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("notification_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "notification_id"),
    )


def downgrade() -> None:
    op.drop_table("notifications")
