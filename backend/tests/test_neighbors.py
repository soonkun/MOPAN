"""Neighbour-chunk expansion.

The fixture is chunked by THE REAL CHUNKER - `build_size_bounded_candidates`, the
same function the ingestion pipeline runs - rather than by hand. That is not
ceremony: expansion's whole heading test is "did the chunker carry an overlap
prefix across this boundary", and a hand-written fixture would let the test agree
with a de-duplicator that does not match what is actually stored. The shapes it
produces are asserted in test_the_fixture_is_the_shape_the_rest_of_this_file_assumes,
so a chunker change breaks that one test loudly instead of quietly hollowing out
the rest.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.chunk import EMBEDDING_DIM, Chunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.user import User
from app.rag.blocks import Block
from app.rag.chunking.structure import build_size_bounded_candidates
from app.retrieval.evidence import RetrievedChunk
from app.retrieval.neighbors import expand, opens_with, strip_overlap
from app.retrieval.service import hybrid_search
from app.retrieval.vector_store import ScoredId, VectorStore

TARGET_CHARS = 160
OVERLAP_CHARS = 50
# Big enough that nothing in this file is ever cut for budget unless a test says so.
WIDE_BUDGET = 100_000

RULE = (
    "복대리인을 선임한 법정대리인의 책임은 원칙적으로 선임 또는 감독에 관한 과실의 유무에 "
    "관계없이 복대리인의 행위 모두에 미친다. "
    "다만, 부득이한 사유가 있는 때는 그 선임감독에 대하여만 책임이 있다. "
    "임의대리인의 경우 복대리인 선임의 책임은 선임 및 감독을 태만히 한 때에만 진다."
)
PERIOD = (
    "특허법상 기간의 계산에 있어서 기간의 초일은 이를 산입하지 아니한다. "
    "기간을 월 또는 연으로 정한 때에는 월 또는 연의 장단에 관계없이 역에 의해 계산한다. "
    "이 경우 최종의 월에 해당일이 없는 때에는 그 월의 말일로 기간이 만료한다. "
    "기간의 말일이 공휴일인 경우에는 기간은 그 다음날로 만료한다."
)
BLOCKS = [
    Block(text="제2장 대리인", block_type="heading", page=67, section="제2장 대리인"),
    Block(text=RULE, block_type="paragraph", page=67, section="제2장 대리인"),
    Block(text="제3장 기간", block_type="heading", page=72, section="제3장 기간"),
    Block(text=PERIOD, block_type="paragraph", page=72, section="제3장 기간"),
]

RULE_CHUNK, PROVISO_CHUNK, PERIOD_CHUNK, DANGLING_CHUNK = 0, 1, 2, 3


def vec(*leading: float) -> list[float]:
    return list(leading) + [0.0] * (EMBEDDING_DIM - len(leading))


class FakeLLMProvider:
    async def embed(self, texts):
        return [vec(1.0) for _ in texts]

    async def chat(self, messages, **kwargs):
        raise NotImplementedError


class FixedVectorStore(VectorStore):
    """Returns exactly the ids it was given, in order, so a test can put a chosen
    chunk at a chosen rank without depending on cosine arithmetic."""

    def __init__(self, chunk_ids):
        self.chunk_ids = chunk_ids

    async def search(self, embedding, limit, collection_ids=None):
        return [ScoredId(chunk_id=cid, score=1.0) for cid in self.chunk_ids][:limit]

    async def upsert(self, items):
        raise NotImplementedError

    async def delete_by_document(self, document_id):
        raise NotImplementedError


@pytest_asyncio.fixture
async def corpus(db):
    user = User(email="neighbors@example.com", password_hash="x", role="admin")
    db.add(user)
    await db.flush()
    collection = Collection(name="심사기준", created_by=user.id)
    db.add(collection)
    await db.flush()

    def _doc(name):
        return Document(
            collection_id=collection.id,
            filename=name,
            file_type="pdf",
            size_bytes=1,
            storage_path="x",
            status="indexed",
            uploaded_by=user.id,
        )

    primary, other = _doc("심사기준.pdf"), _doc("다른문서.pdf")
    db.add_all([primary, other])
    await db.flush()

    candidates = build_size_bounded_candidates(BLOCKS, 1300, TARGET_CHARS, OVERLAP_CHARS)
    for index, candidate in enumerate(candidates):
        db.add(
            Chunk(
                document_id=primary.id,
                chunk_index=index,
                content=candidate.content,
                token_count=candidate.token_count,
                char_count=candidate.char_count,
                page=candidate.page,
                section=candidate.section,
                chunk_metadata={},
                embedding=vec(1.0),
            )
        )
    # A SECOND document occupying the index one past the first document's last
    # chunk. Without the document in expansion's key, expanding the last chunk of
    # 심사기준.pdf forward would find this row and splice another document into
    # the citation - see test_expansion_does_not_cross_a_document_boundary.
    #
    # It is built to look EXACTLY like a legitimate size-split continuation of
    # that chunk: same section, and an opening line that repeats the previous
    # chunk's tail the way the chunker's overlap prefix does. Otherwise the
    # overlap test blocks it on its own and "does not cross a document boundary"
    # would pass with the document dropped from the key - the trap this file's
    # break-it-and-watch-it-fail pass caught.
    tail = candidates[-1].content[-OVERLAP_CHARS:]
    for index in (len(candidates) - 1, len(candidates)):
        db.add(
            Chunk(
                document_id=other.id,
                chunk_index=index,
                content=f"{tail}\n다른 문서의 {index}번째 청크입니다. 이 경우 앞의 내용과 무관하다.",
                token_count=40,
                char_count=40 + len(tail),
                page=1,
                section=candidates[-1].section,
                chunk_metadata={},
                embedding=vec(0.0, 1.0),
            )
        )
    await db.commit()
    rows = (
        await db.execute(
            select(Chunk).where(Chunk.document_id == primary.id).order_by(Chunk.chunk_index)
        )
    ).scalars().all()
    return {"collection": collection, "document": primary, "other": other, "chunks": rows}


def retrieved(chunk: Chunk) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(chunk.id),
        document_id=str(chunk.document_id),
        filename="심사기준.pdf",
        content=chunk.content,
        page=chunk.page,
        section=chunk.section,
        chunk_index=chunk.chunk_index,
    )


async def run(db, chunks, *, mode, budget=WIDE_BUDGET, query="복대리인 책임"):
    items = [retrieved(chunk) for chunk in chunks]
    await expand(
        db, items, mode=mode, overlap_chars=OVERLAP_CHARS, token_budget=budget, query=query
    )
    return items


# --- the fixture's own shape --------------------------------------------------


async def test_the_fixture_is_the_shape_the_rest_of_this_file_assumes(corpus):
    chunks = corpus["chunks"]
    assert len(chunks) == 4
    # The rule chunk was opened by a heading, so it carries no overlap prefix.
    assert strip_overlap("", chunks[RULE_CHUNK].content, OVERLAP_CHARS) is None
    # The proviso chunk was opened by the SIZE bound, so it does.
    proviso = strip_overlap(
        chunks[RULE_CHUNK].content, chunks[PROVISO_CHUNK].content, OVERLAP_CHARS
    )
    assert proviso is not None and proviso.startswith("다만,")
    # The next chapter was opened by a heading: no prefix, which is the boundary.
    assert (
        strip_overlap(chunks[PROVISO_CHUNK].content, chunks[PERIOD_CHUNK].content, OVERLAP_CHARS)
        is None
    )
    dangling = strip_overlap(
        chunks[PERIOD_CHUNK].content, chunks[DANGLING_CHUNK].content, OVERLAP_CHARS
    )
    assert dangling is not None and dangling.startswith("이 경우")


# --- overlap de-duplication ---------------------------------------------------


async def test_the_overlap_is_not_duplicated_when_neighbours_are_joined(db, corpus):
    chunks = corpus["chunks"]
    [item] = await run(db, [chunks[RULE_CHUNK]], mode="targeted")
    assert item.neighbors, "the proviso neighbour should have been merged"
    # The repeated tail is the head of the stored proviso chunk, up to its first
    # newline. It is in the merged text exactly once - it came from the rule
    # chunk, and the copy the chunker made was dropped.
    repeated = chunks[PROVISO_CHUNK].content.split("\n")[0].strip()
    assert repeated
    assert item.content.count(repeated) == 1
    assert "다만, 부득이한 사유가 있는 때는" in item.content
    # And nothing was lost in the joining: the whole of both chunks is there.
    assert item.content.startswith(chunks[RULE_CHUNK].content)
    assert item.content.endswith("태만히 한 때에만 진다.")


def test_strip_overlap_reports_no_repeat_between_unrelated_text():
    assert strip_overlap("전혀 다른 문장이다.", "이어지는 다른 문단.\n본문.", 50) is None


def test_strip_overlap_matches_a_whitespace_normalised_repeat():
    # _sentence_aligned_tail rejoins the tail's sentences with a single space, so
    # the newlines inside the repeat do not survive into the next chunk and a
    # verbatim comparison would miss it.
    previous = "첫 문장이다.\n둘째 문장이다."
    following = "첫 문장이다. 둘째 문장이다.\n셋째 문장이다."
    assert strip_overlap(previous, following, 50) == "셋째 문장이다."


def test_opens_with_needs_the_marker_at_the_start():
    assert opens_with("  다만, 예외가 있다.", ("다만",)) == "다만"
    assert opens_with("본문 중간의 다만은 표지가 아니다.", ("다만",)) is None


# --- boundaries ---------------------------------------------------------------


async def test_expansion_does_not_cross_a_document_boundary(db, corpus):
    chunks = corpus["chunks"]
    last = chunks[-1]
    # The other document HAS a chunk at last.chunk_index + 1 (see the fixture),
    # so "nothing was merged forward" is a statement about the boundary and not
    # about an empty table.
    other_next = (
        await db.execute(
            select(Chunk).where(
                Chunk.document_id == corpus["other"].id,
                Chunk.chunk_index == last.chunk_index + 1,
            )
        )
    ).scalar_one()
    [item] = await run(db, [last], mode="blanket")
    assert "다른 문서의" not in item.content
    assert all(entry["offset"] != 1 for entry in item.neighbors)
    assert all(entry["chunk_id"] != str(other_next.id) for entry in item.neighbors)


async def test_expansion_does_not_cross_a_heading_boundary(db, corpus):
    chunks = corpus["chunks"]
    # The proviso chunk is followed by a chunk the chunker opened at a heading -
    # a boundary the DOCUMENT drew - so blanket, which joins whatever it may,
    # still must not reach across it.
    [item] = await run(db, [chunks[PROVISO_CHUNK]], mode="blanket")
    assert "제3장 기간" not in item.content
    assert [entry["offset"] for entry in item.neighbors] == [-1]


async def test_a_heading_opened_chunk_is_not_pulled_backwards_either(db, corpus):
    [item] = await run(db, [corpus["chunks"][PERIOD_CHUNK]], mode="blanket")
    # Nothing before it (heading boundary), only the dangling chunk after it.
    assert [entry["offset"] for entry in item.neighbors] == [1]
    assert "복대리인" not in item.content


# --- targeted vs blanket ------------------------------------------------------


async def test_targeted_joins_the_next_chunk_when_it_opens_with_a_proviso(db, corpus):
    [item] = await run(db, [corpus["chunks"][RULE_CHUNK]], mode="targeted")
    assert [entry["reason"] for entry in item.neighbors] == ["다만"]
    assert [entry["offset"] for entry in item.neighbors] == [1]


async def test_targeted_joins_the_previous_chunk_when_this_one_dangles(db, corpus):
    [item] = await run(db, [corpus["chunks"][DANGLING_CHUNK]], mode="targeted")
    assert [entry["reason"] for entry in item.neighbors] == ["이 경우"]
    assert [entry["offset"] for entry in item.neighbors] == [-1]
    assert "기간의 초일은 이를 산입하지 아니한다" in item.content


async def test_targeted_leaves_a_chunk_with_no_marker_alone(db, corpus):
    # The proviso chunk's own body opens "다만" - a proviso, not a dangling
    # reference - and the chunk after it is behind a heading. Targeted therefore
    # has nothing to do here, where blanket joins the chunk before it.
    [targeted] = await run(db, [corpus["chunks"][PROVISO_CHUNK]], mode="targeted")
    assert targeted.neighbors == []
    assert targeted.content == corpus["chunks"][PROVISO_CHUNK].content
    [blanket] = await run(db, [corpus["chunks"][PROVISO_CHUNK]], mode="blanket")
    assert blanket.neighbors != []


async def test_off_changes_nothing(db, corpus):
    chunks = corpus["chunks"]
    items = await run(db, chunks, mode="off")
    assert [item.content for item in items] == [chunk.content for chunk in chunks]
    assert all(item.neighbors == [] for item in items)
    # ...and there WAS something to expand, so the assertions above are not
    # passing vacuously.
    expanded = await run(db, chunks, mode="targeted")
    assert any(item.neighbors for item in expanded)


# --- identity -----------------------------------------------------------------


async def test_an_expanded_item_still_names_the_primary_chunk(db, corpus):
    chunk = corpus["chunks"][RULE_CHUNK]
    [item] = await run(db, [chunk], mode="targeted")
    assert item.neighbors
    assert item.chunk_id == str(chunk.id)
    assert item.chunk_index == chunk.chunk_index
    assert item.page == chunk.page
    assert item.section == chunk.section
    assert item.document_id == str(chunk.document_id)


async def test_the_merged_neighbour_is_recorded_in_the_metadata(db, corpus):
    chunks = corpus["chunks"]
    [item] = await run(db, [chunks[RULE_CHUNK]], mode="targeted")
    [entry] = item.neighbors
    assert entry["chunk_id"] == str(chunks[PROVISO_CHUNK].id)
    assert entry["chunk_index"] == chunks[PROVISO_CHUNK].chunk_index
    assert entry["offset"] == 1
    assert entry["page"] == chunks[PROVISO_CHUNK].page
    assert entry["reason"] == "다만"
    assert entry["tokens"] > 0


# --- through the shipped search path ------------------------------------------


async def _hybrid(db, corpus, **kwargs):
    ordered = [str(chunk.id) for chunk in corpus["chunks"]]
    return await hybrid_search(
        db,
        FixedVectorStore(ordered),
        FakeLLMProvider(),
        None,
        "복대리인 책임",
        top_n=kwargs.pop("top_n", 4),
        rrf_k=60,
        candidate_limit=20,
        **kwargs,
    )


async def test_expansion_neither_adds_nor_reorders_citations(db, corpus):
    plain = await _hybrid(db, corpus)
    expanded = await _hybrid(
        db,
        corpus,
        neighbor_expansion="targeted",
        chunk_overlap=OVERLAP_CHARS,
        token_budget=WIDE_BUDGET,
    )
    assert [item.ref for item in expanded] == [item.ref for item in plain]
    assert [item.metadata["chunk_id"] for item in expanded] == [
        item.metadata["chunk_id"] for item in plain
    ]
    # The citation numbering is positional, so equal length and equal order IS
    # the guarantee - and the run has to have actually expanded something.
    assert any(item.metadata["neighbors"] for item in expanded)


async def test_hybrid_search_leaves_content_untouched_when_expansion_is_off(db, corpus):
    plain = await _hybrid(db, corpus)
    off = await _hybrid(
        db, corpus, neighbor_expansion="off", chunk_overlap=OVERLAP_CHARS, token_budget=WIDE_BUDGET
    )
    assert [item.content for item in off] == [item.content for item in plain]
    assert all(item.metadata["neighbors"] == [] for item in off)
    stored = {str(chunk.id): chunk.content for chunk in corpus["chunks"]}
    assert all(item.content == stored[item.metadata["chunk_id"]] for item in off)


async def test_evidence_metadata_carries_the_merge_through_to_the_trace(db, corpus):
    expanded = await _hybrid(
        db,
        corpus,
        neighbor_expansion="targeted",
        chunk_overlap=OVERLAP_CHARS,
        token_budget=WIDE_BUDGET,
    )
    merged = [item for item in expanded if item.metadata["neighbors"]]
    assert merged
    for item in merged:
        for entry in item.metadata["neighbors"]:
            assert set(entry) == {"chunk_id", "chunk_index", "offset", "page", "reason", "tokens"}
            uuid.UUID(entry["chunk_id"])


# --- budget -------------------------------------------------------------------


async def test_the_token_budget_stops_expansion(db, corpus):
    chunks = corpus["chunks"]
    generous = await run(db, chunks, mode="blanket")
    assert sum(1 for item in generous if item.neighbors) >= 2

    # A budget that covers the unexpanded set and nothing more.
    tight = await run(db, chunks, mode="blanket", budget=1)
    assert all(item.neighbors == [] for item in tight)
    # And every primary chunk is still there, unshortened: expansion may lose its
    # own additions, never someone else's evidence.
    assert [item.content for item in tight] == [chunk.content for chunk in chunks]


async def test_a_budget_that_fits_one_expansion_spends_it_on_one(db, corpus):
    from app.core.tokens import count_tokens
    from app.retrieval.neighbors import FENCE_RESERVE_TOKENS, PER_ITEM_OVERHEAD_TOKENS

    chunks = corpus["chunks"]
    query = "복대리인 책임"
    floor = (
        FENCE_RESERVE_TOKENS
        + count_tokens(query)
        + PER_ITEM_OVERHEAD_TOKENS * len(chunks)
        + sum(count_tokens(chunk.content) for chunk in chunks)
    )
    # One rule-chunk expansion costs the proviso chunk's body; give the budget
    # exactly that much headroom and no more.
    [full] = await run(db, [chunks[RULE_CHUNK]], mode="targeted")
    one_expansion = count_tokens(full.content) - count_tokens(chunks[RULE_CHUNK].content)

    items = await run(db, chunks, mode="blanket", budget=floor + one_expansion, query=query)
    spent = sum(
        entry["tokens"] for item in items for entry in item.neighbors
    )
    assert 0 < sum(1 for item in items if item.neighbors) < len(chunks)
    assert spent > 0


@pytest.mark.parametrize("mode", ["off", "targeted", "blanket"])
async def test_expansion_survives_an_item_with_no_chunk_index(db, corpus, mode):
    # A Reranker or an MCP path may hand back a RetrievedChunk built by hand.
    # There is no index to address a neighbour by, so it is left alone rather
    # than guessed at.
    item = retrieved(corpus["chunks"][RULE_CHUNK])
    item.chunk_index = None
    await expand(
        db, [item], mode=mode, overlap_chars=OVERLAP_CHARS, token_budget=WIDE_BUDGET, query="q"
    )
    assert item.neighbors == []
    assert item.content == corpus["chunks"][RULE_CHUNK].content


# --- the reserve, derived rather than restated -------------------------------
#
# FENCE_RESERVE_TOKENS is a literal in a module that cannot import the code it
# describes (app.retrieval must not import app.chat). These two tests are the
# import that module cannot make: they recompute the same facts from
# app.chat.prompt and fail the moment either moves. An earlier version of that
# constant also carried the system prompt's token count and was wrong within a
# day of someone editing the prose - with no test to say so.


def test_the_fence_reserve_still_matches_what_build_prompt_charges():
    from app.chat.prompt import _fence, new_nonce
    from app.core.tokens import count_tokens
    from app.retrieval.neighbors import FENCE_RESERVE_TOKENS

    # The same expression build_prompt uses, against the same one-character body.
    charged = count_tokens(_fence(new_nonce(), "x")) - count_tokens("x")
    assert FENCE_RESERVE_TOKENS >= charged, (
        f"build_prompt now charges {charged} tokens for the fence and expansion "
        f"reserves {FENCE_RESERVE_TOKENS}; expansion can push an evidence item "
        "off the end of the prompt."
    )


def test_the_system_prompt_is_still_free_of_the_evidence_budget():
    from app.chat.prompt import ANSWER_SYSTEM_PROMPT, MANDATORY_TOKEN_ALLOWANCE
    from app.core.tokens import count_tokens

    # Expansion reserves NOTHING for the system prompt, because below this
    # allowance it costs the evidence budget nothing. Past it the excess comes
    # out of ANSWER_CONTEXT_TOKEN_BUDGET and the reserve would have to carry it
    # again - so this is the assumption, stated where it breaks.
    assert count_tokens(ANSWER_SYSTEM_PROMPT) <= MANDATORY_TOKEN_ALLOWANCE
