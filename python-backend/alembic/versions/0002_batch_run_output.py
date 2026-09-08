"""审批批次补 run_output 列(spec #7:durable 重放的子图输出缓存)。

resume 时 Pregel 重放 execute_slice 节点;子图不重跑(批次存在即跳过),
但其输出(answer/executed)须随批次落库,否则每次 resume 都会丢失切片答复。
Revision ID: 0002_batch_run_output
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0002_batch_run_output"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("approval_batches", sa.Column("run_output", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("approval_batches", "run_output")
