"""code_profiles.usage - 코드 탭(OpenCode)이 llm 프록시로 쓴 토큰 누계 {prompt_tokens, completion_tokens}.
채팅은 messages.usage, 딥 리서치는 research_runs.usage에 이미 있고, 코드 탭만 스트림을 그대로 흘려 기록이 없었다.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "code_profiles",
        sa.Column("usage", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("code_profiles", "usage")
