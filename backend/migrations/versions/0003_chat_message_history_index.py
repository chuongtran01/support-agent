"""index for loading a chat session's messages in order

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.create_index("chat_messages_session_id_id_idx", "chat_messages", ["session_id", "id"])


def downgrade() -> None:
    op.drop_index("chat_messages_session_id_id_idx", table_name="chat_messages")
