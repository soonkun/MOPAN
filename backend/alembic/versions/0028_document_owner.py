"""documents.owner_id - 개인 문서(NULL=공용 코퍼스). 기존 행은 전부 공용이라 backfill 없음.

docs/superpowers/plans/2026-09-25-personal-documents.md
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_documents_owner_id_users", "documents", "users", ["owner_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_owner_id", table_name="documents")
    op.drop_constraint("fk_documents_owner_id_users", "documents", type_="foreignkey")
    op.drop_column("documents", "owner_id")
