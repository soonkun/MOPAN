"""documents.structure + chunk_edges - per-document structure detection and the
reference backbone

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

# WHY. A reference-dependent document - a statute, an examination standard, a
# company rulebook - is written to be incomplete: item 3 says "1항의 내용 중 ~~~의
# 경우는 예외로 한다" and has no way to know what 1항 said. Measured on this corpus,
# 상표심사기준 p.89 chunk 399 holds the answer to the owner's question and contains
# neither 상표등록출원서 nor 제36조; its governing clause is three chunks away, out of
# reach of the +/-1 neighbour expansion.
#
# `documents.structure` records what was DETECTED about one document (per document,
# because the `일반` collection already mixes characters) and what a person
# OVERRODE. It is rendered on the document screen - an automatic decision nobody
# can see is exactly the failure 0013 was written to undo.
#
# `chunk_edges` holds the hierarchy and the citations as one edge table, walked
# with a recursive CTE. Deliberately NOT Neo4j: no measurement yet says a graph
# database is needed, and this table is part of how that gets decided.
#
# NOTHING IS RE-CHUNKED BY THIS MIGRATION. Both objects start empty and a document
# is re-cut when it is re-ingested, which is a deliberate act - the same contract
# 0013 has.


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "structure",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_table(
        "chunk_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("src_chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        # NULLABLE on purpose: an unresolved citation is a fact about the corpus,
        # not an error. [민법950] names a statute nobody has uploaded, and the
        # document screen has to be able to say so.
        sa.Column("dst_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("target_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["src_chunk_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dst_chunk_id"], ["chunks.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_chunk_edges_src", "chunk_edges", ["src_chunk_id", "kind"])
    op.create_index("ix_chunk_edges_document_id", "chunk_edges", ["document_id"])
    # Deleting a chunk cascades to the edges pointing AT it; without this that is
    # a sequential scan of the edge table per chunk during every re-ingest.
    op.create_index("ix_chunk_edges_dst", "chunk_edges", ["dst_chunk_id"])


def downgrade() -> None:
    op.drop_index("ix_chunk_edges_dst", table_name="chunk_edges")
    op.drop_index("ix_chunk_edges_document_id", table_name="chunk_edges")
    op.drop_index("ix_chunk_edges_src", table_name="chunk_edges")
    op.drop_table("chunk_edges")
    op.drop_column("documents", "structure")
