"""documents - 규정 현행화(계보·버전·현행 표시)와 중복 감지(sha256)

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

# WHY. docs/superpowers/plans/2026-09-08-document-folders.md 2단계. 개정 규정을 "새 버전으로
# 교체"하면 새 행·새 청크가 생기고 옛 행은 is_current=false로 내려가 검색 두 팔과 표 조회에서
# 빠진다. 덮어쓰지 않는 이유: 답변 이력의 citations가 가리키는 청크가 살아야 과거 답을
# 검증할 수 있다(프롬프트·워크플로우 버전과 같은 원칙). 기존 문서는 각자 계보 1·버전 1·현행.


def upgrade() -> None:
    op.add_column("documents", sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("documents", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("documents", sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("documents", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("effective_date", sa.Date(), nullable=True))
    op.add_column("documents", sa.Column("sha256", sa.String(64), nullable=True))
    op.execute("UPDATE documents SET lineage_id = id WHERE lineage_id IS NULL")
    op.alter_column("documents", "lineage_id", nullable=False)
    op.create_index("ix_documents_lineage_id", "documents", ["lineage_id"])
    op.create_index("uq_documents_lineage_current", "documents", ["lineage_id"], unique=True, postgresql_where=sa.text("is_current"))
    op.create_index("ix_documents_sha256", "documents", ["sha256"])


def downgrade() -> None:
    for name in ("ix_documents_sha256", "uq_documents_lineage_current", "ix_documents_lineage_id"):
        op.drop_index(name, table_name="documents")
    for col in ("sha256", "effective_date", "superseded_at", "is_current", "version", "lineage_id"):
        op.drop_column("documents", col)
