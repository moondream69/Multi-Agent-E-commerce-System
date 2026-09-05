"""add approval_requests table

Revision ID: c3d8e2f4a1b6
Revises: b7c2d9e4f1a8
Create Date: 2026-09-05 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d8e2f4a1b6"
down_revision: str | Sequence[str] | None = "b7c2d9e4f1a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("toolName", sa.String(length=255), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("agentId", sa.String(length=255), nullable=True),
        sa.Column("taskId", sa.String(length=255), nullable=True),
        sa.Column("requestedBy", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "expired",
                "shadow",
                "executed",
                name="approval_requests_status_enum",
            ),
            nullable=False,
        ),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("decidedBy", sa.String(length=255), nullable=True),
        sa.Column("decidedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("createdAt", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updatedAt", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_approval_status", "approval_requests", ["status"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_approval_status", table_name="approval_requests")
    op.drop_table("approval_requests")
    op.execute("DROP TYPE IF EXISTS approval_requests_status_enum")
