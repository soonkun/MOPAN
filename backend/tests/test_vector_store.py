import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.chunk import EMBEDDING_DIM, Chunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.user import User
from app.retrieval.vector_store import PgVectorStore, VectorItem


def vec(*leading: float) -> list[float]:
    """A full-width unit-ish vector: inserting a 3-dim list into Vector(1536)
    fails with `expected 1536 dimensions`."""
    return list(leading) + [0.0] * (EMBEDDING_DIM - len(leading))


@pytest_asyncio.fixture
async def seeded(db):
    user = User(email="vs@example.com", password_hash="x", role="admin")
    db.add(user)
    await db.flush()
    collection_a = Collection(name="A", created_by=user.id)
    collection_b = Collection(name="B", created_by=user.id)
    db.add_all([collection_a, collection_b])
    await db.flush()

    def _doc(collection):
        return Document(
            collection_id=collection.id,
            filename="d.txt",
            file_type="txt",
            size_bytes=1,
            storage_path="x",
            status="indexed",
            uploaded_by=user.id,
        )

    doc_a, doc_b = _doc(collection_a), _doc(collection_b)
    db.add_all([doc_a, doc_b])
    await db.commit()
    return {"a": collection_a, "b": collection_b, "doc_a": doc_a, "doc_b": doc_b}


async def test_upsert_then_search_returns_the_nearest_chunk_first(db, seeded):
    store = PgVectorStore(db)
    await store.upsert(
        [
            VectorItem(
                document_id=seeded["doc_a"].id,
                chunk_index=0,
                content="tomato blight treatment guide",
                token_count=5,
                char_count=30,
                page=1,
                section=None,
                metadata={},
                embedding=vec(1.0, 0.0, 0.0),
            ),
            VectorItem(
                document_id=seeded["doc_a"].id,
                chunk_index=1,
                content="unrelated financial report",
                token_count=4,
                char_count=26,
                page=2,
                section=None,
                metadata={},
                embedding=vec(0.0, 1.0, 0.0),
            ),
        ]
    )
    await db.commit()

    results = await store.search(vec(1.0, 0.0, 0.0), limit=2)

    assert len(results) == 2
    chunk = await db.get(Chunk, uuid.UUID(results[0].chunk_id))
    assert chunk.content == "tomato blight treatment guide"
    assert results[0].score >= results[1].score
    # The vector has to survive the round trip at full width, or retrieval scores
    # the wrong thing forever and nothing raises.
    assert len(chunk.embedding) == EMBEDDING_DIM
    assert list(chunk.embedding) == vec(1.0, 0.0, 0.0)


async def test_search_filters_by_collection(db, seeded):
    store = PgVectorStore(db)
    await store.upsert(
        [
            VectorItem(
                document_id=seeded["doc_a"].id,
                chunk_index=0,
                content="in A",
                token_count=2,
                char_count=4,
                page=None,
                section=None,
                metadata={},
                embedding=vec(1.0),
            ),
            VectorItem(
                document_id=seeded["doc_b"].id,
                chunk_index=0,
                content="in B",
                token_count=2,
                char_count=4,
                page=None,
                section=None,
                metadata={},
                embedding=vec(1.0),
            ),
        ]
    )
    await db.commit()

    results = await store.search(vec(1.0), limit=10, collection_ids=[seeded["a"].id])

    assert len(results) == 1
    chunk = await db.get(Chunk, uuid.UUID(results[0].chunk_id))
    assert chunk.content == "in A"

    # An empty scope means "no collections", not "every collection". Under a
    # truthiness check this returns both chunks - a default-open widening of the
    # one filter Slice 3's Super Agent relies on.
    assert await store.search(vec(1.0), limit=10, collection_ids=[]) == []
    # Unscoped is still the way to ask for everything.
    assert len(await store.search(vec(1.0), limit=10)) == 2


async def test_delete_by_document_makes_reindexing_idempotent(db, seeded):
    store = PgVectorStore(db)
    item = VectorItem(
        document_id=seeded["doc_a"].id,
        chunk_index=0,
        content="first",
        token_count=1,
        char_count=5,
        page=None,
        section=None,
        metadata={},
        embedding=vec(1.0),
    )
    await store.upsert([item])
    await db.commit()

    await store.delete_by_document(seeded["doc_a"].id)
    await store.upsert([item])
    await db.commit()

    rows = (await db.scalars(select(Chunk).where(Chunk.document_id == seeded["doc_a"].id))).all()
    assert len(rows) == 1


async def test_delete_by_document_leaves_other_documents_alone(db, seeded):
    store = PgVectorStore(db)

    def _item(document_id, content):
        return VectorItem(
            document_id=document_id,
            chunk_index=0,
            content=content,
            token_count=1,
            char_count=len(content),
            page=None,
            section=None,
            metadata={},
            embedding=vec(1.0),
        )

    await store.upsert([_item(seeded["doc_a"].id, "in A"), _item(seeded["doc_b"].id, "in B")])
    await db.commit()

    await store.delete_by_document(seeded["doc_a"].id)
    await db.commit()

    remaining = (await db.scalars(select(Chunk))).all()
    assert [c.content for c in remaining] == ["in B"]


async def test_upsert_replaces_a_chunk_at_the_same_index(db, seeded):
    """`upsert` has to mean upsert. A Qdrant backend would overwrite by id; if
    the pgvector one raises IntegrityError instead, the interface is not
    swappable and a re-index that skips delete_by_document dies on a unique
    violation."""
    store = PgVectorStore(db)

    def _item(content, embedding):
        return VectorItem(
            document_id=seeded["doc_a"].id,
            chunk_index=0,
            content=content,
            token_count=1,
            char_count=len(content),
            page=None,
            section=None,
            metadata={},
            embedding=embedding,
        )

    await store.upsert([_item("first", vec(1.0))])
    await db.commit()
    await store.upsert([_item("second", vec(0.0, 1.0))])
    await db.commit()

    rows = (await db.scalars(select(Chunk))).all()
    assert [c.content for c in rows] == ["second"]
    assert list(rows[0].embedding) == vec(0.0, 1.0)


async def test_search_with_no_data_returns_empty(db, seeded):
    assert await PgVectorStore(db).search(vec(1.0), limit=5) == []


async def test_upsert_of_nothing_is_a_no_op(db, seeded):
    await PgVectorStore(db).upsert([])
    await db.commit()
    assert (await db.scalars(select(Chunk))).all() == []


async def test_upsert_rejects_a_duplicate_chunk_index(db, seeded):
    item = VectorItem(
        document_id=seeded["doc_a"].id,
        chunk_index=0,
        content="dup",
        token_count=1,
        char_count=3,
        page=None,
        section=None,
        metadata={},
        embedding=vec(1.0),
    )
    # Postgres would raise CardinalityViolationError; Qdrant would last-write-win.
    # Rejecting here keeps the interface's behaviour the same on both.
    with pytest.raises(ValueError, match="unique by"):
        await PgVectorStore(db).upsert([item, item])
