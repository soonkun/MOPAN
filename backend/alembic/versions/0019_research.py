"""딥 리서치 - 방(projects), 지침 버전, 실행(runs)

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

# WHY. 새싹이(saessagi)의 딥 리서치를 이식한다(docs/superpowers/plans/2026-09-08-deep-research.md).
# 원본은 SQLite 세 표(projects/instruction_versions/turns)였고 여기서는 Postgres 세 표다.
# 실행은 요청 수명이 아니라 arq 잡이라 진행 로그(steps)가 행에 남고, 탭을 닫아도 이어 본다.


def upgrade() -> None:
    op.create_table(
        "research_projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # sub_queries / top_k / gap_rounds / max_evidence_chunks - store.LIMITS로 클램프.
        sa.Column("budget", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("reasoning_effort", sa.String(20), nullable=True),
        sa.Column("gap_model", sa.String(100), nullable=True),
        sa.Column("collection_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("name", name="uq_research_projects_name"),
    )
    op.create_table(
        "research_instructions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("project_id", "version", name="uq_research_instructions_version"),
    )
    op.create_index(
        "uq_research_instructions_active", "research_instructions", ["project_id"], unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "research_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("attachment_text", sa.Text(), nullable=True),
        sa.Column("attachment_name", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("steps", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("report", sa.Text(), nullable=True),
        sa.Column("sources", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("sub_queries", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("reasoning_effort", sa.String(20), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','planning','searching','gap','synthesis','done','failed','cancelled')",
            name="ck_research_runs_status",
        ),
    )
    # FK마다 선행 인덱스(tests/test_schema.py 규칙). runs.created_by는 (created_by, status) 복합이 맡는다.
    op.create_index("ix_research_projects_created_by", "research_projects", ["created_by"])
    op.create_index("ix_research_instructions_created_by", "research_instructions", ["created_by"])
    op.create_index("ix_research_runs_project_created", "research_runs", ["project_id", "created_at"])
    op.create_index("ix_research_runs_created_by_status", "research_runs", ["created_by", "status"])


def downgrade() -> None:
    op.drop_table("research_runs")
    op.drop_index("uq_research_instructions_active", table_name="research_instructions")
    op.drop_table("research_instructions")
    op.drop_table("research_projects")
