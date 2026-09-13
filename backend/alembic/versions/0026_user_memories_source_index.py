"""user_memories.source_conversation_id 인덱스.

0024가 빠뜨렸다 - 대화를 지우면 SET NULL이 이 열을 훑는데 인덱스가 없었다(test_schema가 잡음).
"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_user_memories_source_conversation_id", "user_memories", ["source_conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_user_memories_source_conversation_id", table_name="user_memories")
