"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "collections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("collections.id")),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("file_type", sa.String(20), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="uploaded"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id")),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', content)", persisted=True),
        ),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(500), nullable=True),
        sa.Column("chunk_metadata", postgresql.JSONB(), server_default="{}"),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_chunks_content_tsv", "chunks", ["content_tsv"], postgresql_using="gin")
    op.execute(
        "CREATE INDEX ix_chunks_embedding ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("title", sa.String(500), server_default="New Chat"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id")),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), server_default="[]"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding")
    op.drop_index("ix_chunks_content_tsv", table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.drop_table("collections")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS vector")
