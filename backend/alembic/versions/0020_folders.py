"""folders - 컬렉션 안의 폴더 트리, documents.folder_id

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

# WHY. docs/superpowers/plans/2026-09-08-document-folders.md. 컬렉션은 처리 방식·권한의
# 단위로 남고, 폴더는 그 안의 정리 수단이다. path는 조상 id를 '/'로 이어 붙인
# materialized path - 하위 트리는 LIKE prefix 한 번, ltree 확장 없이. folder_id NULL은 루트.


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("folders.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("depth", sa.SmallInteger(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("collection_id", "parent_id", "name", name="uq_folders_sibling_name"),
        sa.CheckConstraint("depth BETWEEN 1 AND 8", name="ck_folders_depth"),
    )
    op.create_index("ix_folders_collection_id", "folders", ["collection_id"])
    op.create_index("ix_folders_parent_id", "folders", ["parent_id"])
    op.create_index("ix_folders_created_by", "folders", ["created_by"])
    op.create_index("ix_folders_path", "folders", ["path"], postgresql_ops={"path": "text_pattern_ops"})
    op.add_column("documents", sa.Column("folder_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("folders.id", ondelete="RESTRICT"), nullable=True))
    op.create_index("ix_documents_folder_id", "documents", ["folder_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_folder_id", table_name="documents")
    op.drop_column("documents", "folder_id")
    for name in ("ix_folders_path", "ix_folders_created_by", "ix_folders_parent_id", "ix_folders_collection_id"):
        op.drop_index(name, table_name="folders")
    op.drop_table("folders")
