"""code_profiles - 코딩 에이전트 사용자 프로필(샌드박스 uid, 내 컴퓨터 연결 토큰 해시).

docs/superpowers/plans/2026-09-24-code-agent.md
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "code_profiles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sandbox_uid", sa.Integer(), nullable=False),
        sa.Column("bridge_token_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("sandbox_uid"),
    )


def downgrade() -> None:
    op.drop_table("code_profiles")
