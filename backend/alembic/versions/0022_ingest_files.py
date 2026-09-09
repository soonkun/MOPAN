"""ingest_files - 감시 폴더의 파일 장부

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-09
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

# WHY. 2만 권을 터널(trycloudflare) 업로드로 넣을 수 없어 서버 폴더를 훑어 등록한다. 매 스캔마다
# 전부 해시하면 60GB를 2분마다 읽게 되므로 (크기, mtime)이 같으면 건너뛰고, 바뀐 것만 해시해
# 중복(sha256이 같은 현행 문서)을 가려낸다. 문서 행은 원본 경로를 storage_path로 참조한다(복사 없음).


def upgrade() -> None:
    op.create_table(
        "ingest_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mtime", sa.Float(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("path", name="uq_ingest_files_path"),
        sa.CheckConstraint("status IN ('registered','duplicate','unsupported','failed','missing')", name="ck_ingest_files_status"),
    )
    op.create_index("ix_ingest_files_document_id", "ingest_files", ["document_id"])
    op.create_index("ix_ingest_files_status", "ingest_files", ["status"])


def downgrade() -> None:
    op.drop_table("ingest_files")
