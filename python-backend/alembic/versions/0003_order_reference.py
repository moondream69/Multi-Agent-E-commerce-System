"""订单补可选 reference 列(spec #8 B8:CSV 订单导入幂等去重键)。

订单无自然键(商品有 sku),导入幂等需要一个业务侧去重键;
CSV 行可携带 reference(如渠道单号),缺失的行不参与去重(全部创建),
故 partial unique 索引只约束非空 reference。

Revision ID: 0003_order_reference
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_order_reference"
down_revision = "0002_batch_run_output"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("reference", sa.String(64), nullable=True))
    op.create_index(
        "uq_orders_reference_partial",
        "orders",
        ["reference"],
        unique=True,
        postgresql_where=sa.text("reference IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_orders_reference_partial", table_name="orders")
    op.drop_column("orders", "reference")
