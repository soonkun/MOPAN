"""conversation_summary - 멀티턴 기억의 롤링 요약

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

# WHY. 답변 프롬프트는 최근 메시지 10개만 원문으로 실었고 그 앞은 통째로 잊었다.
# 긴 상담(되묻기 왕복 뒤의 본 질문, 앞서 정한 조건)이 열 메시지를 넘는 순간 모델은
# 대화의 앞부분을 모른 채 답했다. 오래된 턴은 대화마다 요약 하나로 접어 둔다
# (app/chat/memory.py). summary_through_at은 요약이 읽은 마지막 메시지의 created_at -
# 그 뒤의 메시지만 다음 갱신에 접는다.


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("conversations", sa.Column("summary_through_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "summary_through_at")
    op.drop_column("conversations", "summary")
