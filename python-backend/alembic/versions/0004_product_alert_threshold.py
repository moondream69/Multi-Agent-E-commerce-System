"""商品加库存告警阈值列(spec #9 A9:阈值按商品设置,扣减后自动比对)。

默认 10 为演练起点值(可经 draft_create/draft_edit 工具或商品 CSV 可选列覆盖)。

Revision ID: 0004_product_alert_threshold
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_product_alert_threshold"
down_revision = "0003_order_reference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("alert_threshold", sa.Integer(), nullable=False, server_default="10"),
    )


def downgrade() -> None:
    op.drop_column("products", "alert_threshold")
