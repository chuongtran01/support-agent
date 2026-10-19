"""refund items, item categories and ticket kinds

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"

ITEM_CATEGORIES = "'gift_card', 'tent', 'pack', 'footwear', 'apparel', 'consumable', 'other'"
TICKET_KINDS = "'support', 'escalation', 'manual_review'"


def upgrade() -> None:
    op.add_column(
        "order_items", sa.Column("category", sa.Text(), server_default="other", nullable=False)
    )
    op.create_check_constraint(
        "order_items_category_check", "order_items", f"category IN ({ITEM_CATEGORIES})"
    )

    # Refunds are per order item; there are no refunds yet, so the new column can be required.
    op.add_column("refunds", sa.Column("order_item_id", sa.Integer(), nullable=False))
    op.create_foreign_key(
        "refunds_order_item_id_fkey", "refunds", "order_items", ["order_item_id"], ["id"]
    )
    op.add_column(
        "refunds", sa.Column("quantity", sa.Integer(), server_default="1", nullable=False)
    )
    op.create_check_constraint("refunds_quantity_check", "refunds", "quantity > 0")

    op.add_column("tickets", sa.Column("kind", sa.Text(), server_default="support", nullable=False))
    op.create_check_constraint("tickets_kind_check", "tickets", f"kind IN ({TICKET_KINDS})")


def downgrade() -> None:
    op.drop_constraint("tickets_kind_check", "tickets")
    op.drop_column("tickets", "kind")
    op.drop_constraint("refunds_quantity_check", "refunds")
    op.drop_column("refunds", "quantity")
    op.drop_constraint("refunds_order_item_id_fkey", "refunds")
    op.drop_column("refunds", "order_item_id")
    op.drop_constraint("order_items_category_check", "order_items")
    op.drop_column("order_items", "category")
