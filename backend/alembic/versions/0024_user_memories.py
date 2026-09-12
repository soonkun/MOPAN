"""user_memories - 대화를 넘어 남는 사용자별 기억

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

# WHY. 멀티턴 기억(0023)은 대화 하나 안에서만 산다. 새 대화를 열면 앱 이름·직무·선호를 다시
# 말해야 했다(소유자 요청 2026-09-13). 사용자마다 "사실 한 줄"의 목록을 두고 매 턴 뒤 값싼
# 모델이 새 사실만 뽑아 붙이며, 답변 시스템 프롬프트에 실린다. 본인이 보고 지운다.


def upgrade() -> None:
    op.create_table(
        "user_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.String(300), nullable=False),
        sa.Column(
            "source_conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
    )
    op.create_index("ix_user_memories_user_id", "user_memories", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_memories_user_id", table_name="user_memories")
    op.drop_table("user_memories")
