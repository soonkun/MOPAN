import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# 'parent' - this chunk sits under that one in the document's own numbering.
# 'ref'    - this chunk cites that one, in words the author wrote.
EDGE_KINDS = ("parent", "ref")


class ChunkEdge(Base):
    """The reference backbone: one table, two kinds of edge, walked with a
    recursive CTE in Postgres.

    NO NEO4J, and that is a decision rather than an omission. Nothing measured so
    far says a graph database is needed, and this table is partly how we find out:
    if resolving citations here moves the `crossref` evaluation group, Postgres is
    enough; if it does not, that is the measured case for a typed entity graph and
    it belongs in a report, not in a dependency added on a hunch.

    `dst_chunk_id` IS NULLABLE ON PURPOSE. An unresolved citation is a FACT about
    this corpus, not an error: 특허·실용신안 심사기준 cites [민법950] and [헌법6], and
    neither statute has been uploaded. Dropping those rows would leave the
    document screen unable to say "1,102 citations found, 913 resolved" - and that
    sentence is the whole reason the user can trust what was inferred.

    Postgres-only, like `content_tsv`. The VectorStore seam keeps pgvector out of
    the pipeline; this table is not behind that seam, because a remote vector
    store has no chunk rows to point at. A second backend would need its own
    answer here, and would be told so by the foreign keys rather than by a comment.
    """

    __tablename__ = "chunk_edges"
    __table_args__ = (
        # The delivery-time lookup: "what does this chunk cite". Declared here and
        # not only in the migration, or the next autogenerate emits DROP INDEX.
        Index("ix_chunk_edges_src", "src_chunk_id", "kind"),
        Index("ix_chunk_edges_document_id", "document_id"),
        # Every foreign key in this schema carries a leading index, and this one
        # is not decoration: deleting a chunk cascades to the rows POINTING AT it,
        # and without the index that is a sequential scan of the whole edge table
        # per chunk - during a re-ingest, once per chunk of the document.
        Index("ix_chunk_edges_dst", "dst_chunk_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    src_chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL, not CASCADE (0015): 문서 간 간선의 주인은 인용하는 문서다. 대상
    # 문서가 재적재되어 청크가 사라지면 이 간선은 "미해소"로 돌아가야지 행째
    # 사라지면 안 된다 - 사라진 적이 있고(재적재 직후 실용신안법 cross-doc 0개),
    # relink가 대상이 다시 색인될 때 도로 잇는다.
    dst_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # The citation EXACTLY AS WRITTEN - "제46조제3항", "[특법54(3)]". It is what the
    # unresolved list shows the user, and it is what makes an edge auditable
    # without re-running the parser.
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    # The parsed path, "조46/항3". Kept even when nothing resolved, so uploading the
    # missing statute later is a re-resolve and not a re-parse.
    target_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
