"""research_scope - 방의 플래너 관점(planner_hint)과 실행의 검토 대상 요약(scope)

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

# WHY. 딥 리서치 재설계(기술 보고서 §13). planner_hint는 원본(새싹이 CR-62)에 있던 "하위 질의를
# 어느 관점으로 나눌지"의 한 줄 - 이식 때 빠졌다. scope는 첨부(과제 계획서)를 먼저 읽어 뽑은
# 구조화 요약(목표·대상·방법·산출물·핵심어) - 하위 질의는 사용자의 한 줄이 아니라 이것에서
# 나오고, 보고서 첫 절 '검토 대상 요약'의 재료가 된다.


def upgrade() -> None:
    op.add_column("research_projects", sa.Column("planner_hint", sa.Text(), nullable=True))
    op.add_column("research_projects", sa.Column("template_id", sa.String(40), nullable=True))
    op.add_column("research_runs", sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("research_runs", "scope")
    op.drop_column("research_projects", "template_id")
    op.drop_column("research_projects", "planner_hint")
