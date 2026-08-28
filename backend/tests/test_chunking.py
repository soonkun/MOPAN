import pytest

from app.core.tokens import count_tokens
from app.rag.blocks import Block
from app.rag.chunking.base import ChunkCandidate
from app.rag.chunking.structure import (
    build_size_bounded_candidates,
    split_sentences,
    split_to_token_limit,
)

MAX_TOKENS = 60

# The pre-fix hard split rode a fixed stride over the token stream, so whether it
# landed mid-character depended on the limit. MAX_TOKENS = 60 happens to be one of
# the ~13% of values that survive intact for these fixtures, which is exactly how
# the corruption shipped unnoticed. 59 corrupts both Hangul and emoji.
CORRUPTING_LIMIT = 59


def _separator_less_document(block_count: int = 200) -> list[Block]:
    """Blocks with no terminal punctuation.

    The under-count the size pass exists to prevent only shows when the joining
    separator is a token the sum omits. A block ending in "." lets cl100k absorb
    the following newline into one token, so period-terminated fixtures hide the
    bug - which is how it survived revision 1.
    """
    return [Block(text="rotate crops", block_type="paragraph") for _ in range(block_count)]


def _heading_less_document(block_count: int = 40) -> list[Block]:
    """The case the old chunker collapsed into a single chunk: a PDF with no
    headings at all."""
    return [
        Block(
            text=(
                f"Paragraph {i}. Tomato blight spreads through infected soil and "
                f"splashing water. Growers should rotate crops and remove debris."
            ),
            block_type="paragraph",
            page=1 + i // 5,
        )
        for i in range(block_count)
    ]


def test_split_sentences_splits_on_terminal_punctuation():
    assert split_sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]


def test_split_sentences_handles_korean_terminators():
    assert len(split_sentences("첫 번째 문장이다. 두 번째 문장이다.")) == 2


def test_split_to_token_limit_returns_the_text_unchanged_when_it_fits():
    assert split_to_token_limit("short text", MAX_TOKENS) == ["short text"]


def test_split_to_token_limit_respects_the_limit_on_long_text():
    text = " ".join(f"Sentence number {i} about tomato blight." for i in range(120))
    pieces = split_to_token_limit(text, MAX_TOKENS)
    assert len(pieces) > 1
    assert all(count_tokens(p) <= MAX_TOKENS for p in pieces)


def test_split_to_token_limit_hard_splits_a_single_oversized_sentence():
    # No sentence boundary to split on: must still respect the limit.
    text = "word " * 500
    pieces = split_to_token_limit(text, MAX_TOKENS)
    assert len(pieces) > 1
    assert all(count_tokens(p) <= MAX_TOKENS for p in pieces)


def test_split_to_token_limit_respects_the_limit_on_boundary_less_korean():
    """cl100k tokenises Hangul below the character level, so a naive stride over
    the token stream lands mid-character and decodes to U+FFFD on both sides.
    Measured against the pre-fix splitter, that corrupted this text at 318 of 512
    max_tokens values - silent data loss in the language this system targets."""
    text = "가나다라마바사아자차카타파하" * 40
    pieces = split_to_token_limit(text, CORRUPTING_LIMIT)

    assert len(pieces) > 1
    assert all(count_tokens(p) <= CORRUPTING_LIMIT for p in pieces)
    assert "".join(pieces) == text
    assert not any("�" in p for p in pieces)


def test_split_to_token_limit_bounds_oversized_whitespace():
    """split_sentences drops whitespace-only fragments, so a whitespace-heavy
    block leaves nothing to rejoin; the fallback must still be size-bounded.

    8000 spaces, not 4000: 4000 encodes to 32 tokens, which sits under the limit
    and returns at the size check without ever reaching the fallback."""
    pieces = split_to_token_limit(" " * 8000, MAX_TOKENS)
    assert count_tokens(" " * 8000) > MAX_TOKENS
    assert all(count_tokens(p) <= MAX_TOKENS for p in pieces)


def test_split_to_token_limit_rejects_a_non_positive_limit():
    # max_chunk_tokens is an operator-facing setting, so 0 is reachable from
    # configuration. `match` matters: the pre-fix code also raised ValueError, but
    # as `range() arg 3 must not be zero` from deep inside a slice.
    with pytest.raises(ValueError, match="max_tokens"):
        split_to_token_limit("some text", 0)


def test_size_pass_produces_many_chunks_for_a_heading_less_document():
    """Regression test for the single worst defect in revision 1: 40 blocks with
    no headings previously became ONE chunk containing the whole document."""
    candidates = build_size_bounded_candidates(_heading_less_document(), MAX_TOKENS)

    assert len(candidates) > 1
    assert all(c.token_count <= MAX_TOKENS for c in candidates)
    assert all(isinstance(c, ChunkCandidate) for c in candidates)


def test_size_pass_token_count_is_an_upper_bound_on_a_re_encode():
    """The running total is what enforces the limit, so it must never sit below
    an exact re-encode of the content it describes. Summing standalone piece
    counts does sit below it - the joining separator is a token the sum omits.

    Uses separator-less blocks: against the pre-fix sum this document produced a
    candidate whose content re-encodes to 89 tokens under a 60-token limit."""
    for candidate in build_size_bounded_candidates(_separator_less_document(), MAX_TOKENS):
        assert count_tokens(candidate.content) <= candidate.token_count <= MAX_TOKENS


def test_size_pass_never_exceeds_the_limit_even_for_one_huge_block():
    # Emoji, not ASCII: cl100k splits one emoji into several tokens, so a stride
    # cut lands mid-character. The pre-fix splitter corrupted this at 13 of the 20
    # limits in 50..69, and dropped the round trip with it.
    text = "🍅🌱🚜" * 200
    blocks = [Block(text=text, block_type="paragraph")]
    candidates = build_size_bounded_candidates(blocks, CORRUPTING_LIMIT)

    assert len(candidates) > 1
    assert all(count_tokens(c.content) <= CORRUPTING_LIMIT for c in candidates)
    assert "".join(c.content for c in candidates) == text
    assert not any("�" in c.content for c in candidates)


def test_size_pass_starts_a_new_candidate_at_every_heading():
    blocks = [
        Block(text="Section A", block_type="heading", section="Section A"),
        Block(text="Body of A.", block_type="paragraph", section="Section A"),
        Block(text="Section B", block_type="heading", section="Section B"),
        Block(text="Body of B.", block_type="paragraph", section="Section B"),
    ]
    candidates = build_size_bounded_candidates(blocks, 1000)
    assert len(candidates) == 2
    assert candidates[0].section == "Section A"
    assert candidates[1].section == "Section B"


def test_size_pass_preserves_page_and_section_for_citations():
    blocks = [
        Block(text="Intro paragraph.", block_type="paragraph", page=32, section="연구 결과"),
    ]
    [candidate] = build_size_bounded_candidates(blocks, 1000)
    assert candidate.page == 32
    assert candidate.section == "연구 결과"


def test_size_pass_on_an_empty_document():
    assert build_size_bounded_candidates([], MAX_TOKENS) == []


def test_size_pass_drops_empty_blocks():
    """A zero-length candidate costs an embedding call and retrieves nothing, and
    an empty leading block would prefix the next one with a stray newline."""
    blocks = [
        Block(text="", block_type="paragraph"),
        Block(text="   ", block_type="paragraph"),
        Block(text="Real text.", block_type="paragraph"),
    ]
    assert [c.content for c in build_size_bounded_candidates(blocks, MAX_TOKENS)] == ["Real text."]


def test_size_pass_breaks_at_a_blank_heading():
    """A parser can emit a heading block whose text is empty - text_parser does it
    for a bare '#' line. Skipping it along with the other blank blocks swallowed
    the section boundary too, so section B's body was appended to section A's
    candidate and cited as page 1 of section A. Only the heading's text is
    missing; the break it marks is not."""
    blocks = [
        Block(text="Body of A.", block_type="paragraph", page=1, section="A"),
        Block(text="  ", block_type="heading", page=2, section="B"),
        Block(text="Body of B.", block_type="paragraph", page=2, section="B"),
    ]
    candidates = build_size_bounded_candidates(blocks, 1000)

    assert [c.content for c in candidates] == ["Body of A.", "Body of B."]
    assert [(c.page, c.section) for c in candidates] == [(1, "A"), (2, "B")]


# --- Task 10: strategies -----------------------------------------------------

from app.rag.chunking import get_chunking_strategy  # noqa: E402
from app.rag.chunking.fixed import FixedChunking  # noqa: E402
from app.rag.chunking.semantic import StructureSemanticChunking  # noqa: E402

# Deterministic fake embeddings: one-hot on a "topic id" baked into the text, so
# tests fully control which candidates look similar.
TOPIC_VECTORS = {"topic-a": [1.0, 0.0, 0.0], "topic-b": [0.0, 1.0, 0.0]}


async def fake_embed_fn(texts: list[str]) -> list[list[float]]:
    return [TOPIC_VECTORS["topic-a"] if "topic-a" in t else TOPIC_VECTORS["topic-b"] for t in texts]


def _half_limit_document(pair_count: int = 6) -> tuple[list[Block], int]:
    """Heading+body pairs whose pass-1 candidate is exactly half the token limit.

    Neither string ends in punctuation, which is what stops cl100k from absorbing
    the joining newline into the preceding token and hiding its cost.
    """
    blocks: list[Block] = []
    for i in range(pair_count):
        blocks.append(Block(text="Alpha", block_type="heading", page=i, section=f"S{i}"))
        blocks.append(Block(text="rotate crops", block_type="paragraph", page=i, section=f"S{i}"))
    return blocks, 2 * count_tokens("Alpha\nrotate crops")


def _korean_headed_document(pair_count: int = 20) -> list[Block]:
    """Headed, so pass 1 leaves candidates small enough for the merge pass to
    actually merge, in a script cl100k tokenises below the character level."""
    blocks: list[Block] = []
    for i in range(pair_count):
        blocks.append(Block(text="장 제목", block_type="heading", page=i, section=f"장 {i}"))
        blocks.append(Block(text="가나다라마바사아자차카타파하", block_type="paragraph", page=i))
    return blocks


def _headed_separator_less_document(pair_count: int = 20) -> list[Block]:
    blocks: list[Block] = []
    for i in range(pair_count):
        blocks.append(Block(text="Field notes", block_type="heading", page=i, section=f"N{i}"))
        blocks.append(Block(text="rotate crops and remove debris", block_type="paragraph", page=i))
    return blocks


def test_fixed_chunking_rejects_an_overlap_at_or_above_the_chunk_size():
    # Reachable: chunk size and overlap are admin-configurable settings.
    with pytest.raises(ValueError):
        FixedChunking(chunk_size=100, overlap=100)
    with pytest.raises(ValueError):
        FixedChunking(chunk_size=100, overlap=-1)


async def test_fixed_chunking_splits_by_size_with_overlap():
    blocks = [Block(text="x" * 1000, block_type="paragraph", page=7, section="S")]
    candidates = await FixedChunking(chunk_size=400, overlap=50).chunk(blocks, fake_embed_fn)

    assert len(candidates) > 1
    assert all(c.char_count <= 400 for c in candidates)


async def test_fixed_chunking_preserves_page_and_section():
    """Without this the Fixed-vs-Semantic comparison view cannot show location
    metadata, and any document processed with Fixed loses citation provenance."""
    blocks = [
        Block(text="a" * 500, block_type="paragraph", page=1, section="First"),
        Block(text="b" * 500, block_type="paragraph", page=9, section="Second"),
    ]
    candidates = await FixedChunking(chunk_size=200, overlap=0).chunk(blocks, fake_embed_fn)

    assert candidates[0].page == 1
    assert candidates[0].section == "First"
    assert any(c.page == 9 and c.section == "Second" for c in candidates)


async def test_semantic_chunking_merges_similar_adjacent_candidates():
    blocks = [
        Block(text="Heading", block_type="heading", section="Heading"),
        Block(text="topic-a sentence one.", block_type="paragraph"),
        Block(text="topic-a sentence two.", block_type="paragraph"),
    ]
    strategy = StructureSemanticChunking(similarity_threshold=0.5, max_chunk_tokens=1000)

    candidates = await strategy.chunk(blocks, fake_embed_fn)

    assert len(candidates) == 1
    assert "sentence one" in candidates[0].content
    assert "sentence two" in candidates[0].content


async def test_semantic_chunking_splits_dissimilar_candidates():
    blocks = [
        Block(text="Heading A", block_type="heading", section="Heading A"),
        Block(text="topic-a sentence.", block_type="paragraph"),
        Block(text="Heading B", block_type="heading", section="Heading B"),
        Block(text="topic-b sentence.", block_type="paragraph"),
    ]
    strategy = StructureSemanticChunking(similarity_threshold=0.9, max_chunk_tokens=1000)

    candidates = await strategy.chunk(blocks, fake_embed_fn)
    assert len(candidates) == 2


async def test_semantic_merge_compares_a_candidate_with_its_predecessors_embedding():
    """A merged candidate's own embedding is cleared, because its text changed.
    Reading the threshold off `previous.embedding or embedding` therefore falls
    back to comparing the incoming candidate with ITSELF - similarity 1.0 - so
    every candidate after the first merge is absorbed regardless of topic, with
    only the token limit left to stop it. Compare against the predecessor's own
    pass-1 embedding instead."""
    blocks = [
        Block(text="Heading A", block_type="heading", section="A"),
        Block(text="topic-a first.", block_type="paragraph"),
        Block(text="Heading B", block_type="heading", section="B"),
        Block(text="topic-a second.", block_type="paragraph"),
        Block(text="Heading C", block_type="heading", section="C"),
        Block(text="topic-b elsewhere.", block_type="paragraph"),
    ]
    strategy = StructureSemanticChunking(similarity_threshold=0.9, max_chunk_tokens=1000)

    candidates = await strategy.chunk(blocks, fake_embed_fn)

    assert len(candidates) == 2
    assert "topic-b" not in candidates[0].content


async def test_semantic_merge_keeps_the_location_it_starts_at():
    """A merged chunk begins where its first candidate began, so that is the
    citation to show. A first candidate with no location of its own inherits the
    absorbed one's rather than dropping provenance altogether."""
    strategy = StructureSemanticChunking(similarity_threshold=0.5, max_chunk_tokens=1000)
    located = [
        Block(text="Heading A", block_type="heading", section="A", page=3),
        Block(text="topic-a first.", block_type="paragraph", section="A", page=3),
        Block(text="Heading B", block_type="heading", section="B", page=7),
        Block(text="topic-a second.", block_type="paragraph", section="B", page=7),
    ]
    [candidate] = await strategy.chunk(located, fake_embed_fn)
    assert (candidate.page, candidate.section) == (3, "A")

    unlocated_first = [
        Block(text="Heading", block_type="heading"),
        Block(text="topic-a first.", block_type="paragraph"),
        Block(text="Heading B", block_type="heading", section="B", page=7),
        Block(text="topic-a second.", block_type="paragraph", section="B", page=7),
    ]
    [candidate] = await strategy.chunk(unlocated_first, fake_embed_fn)
    assert (candidate.page, candidate.section) == (7, "B")


async def test_semantic_merge_charges_the_joining_newline():
    """Task 9's defect, one pass later: summing two candidate token counts omits
    the newline the merge joins them with, and an under-count is exactly how a
    chunk gets past the limit it is supposed to enforce. Against the un-charged
    sum this document yields 9-token candidates under an 8-token limit."""
    blocks, limit = _half_limit_document()
    strategy = StructureSemanticChunking(similarity_threshold=0.5, max_chunk_tokens=limit)

    candidates = await strategy.chunk(blocks, fake_embed_fn)

    assert candidates
    for candidate in candidates:
        assert count_tokens(candidate.content) <= candidate.token_count <= limit


async def test_semantic_chunking_bounds_adversarial_corpora():
    """The bound has to hold on the shapes that hide a missing separator cost:
    Korean (tokenised below the character level), text with no terminal
    punctuation, and a document with no headings at all."""
    corpora = {
        "korean": _korean_headed_document(),
        "separator-less": _headed_separator_less_document(),
        "no-boundary": _separator_less_document(),
        "heading-less": _heading_less_document(),
    }
    strategy = StructureSemanticChunking(similarity_threshold=0.5, max_chunk_tokens=MAX_TOKENS)

    for name, blocks in corpora.items():
        candidates = await strategy.chunk(blocks, fake_embed_fn)
        assert len(candidates) > 1, name
        for candidate in candidates:
            assert count_tokens(candidate.content) <= candidate.token_count <= MAX_TOKENS, name


async def test_semantic_chunking_bounds_a_heading_less_document():
    """The end-to-end version of the Task 9 regression: the full strategy, not
    just the size pass, must never emit an over-limit chunk."""
    strategy = StructureSemanticChunking(similarity_threshold=0.99, max_chunk_tokens=MAX_TOKENS)

    candidates = await strategy.chunk(_heading_less_document(), fake_embed_fn)

    assert len(candidates) > 1
    assert all(c.token_count <= MAX_TOKENS for c in candidates)


async def test_semantic_chunking_embeds_the_document_once():
    """One batched call, not one per adjacent pair - the pair-wise shape costs an
    API round trip per candidate on every document the worker ingests."""
    calls: list[int] = []

    async def counting_embed_fn(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        return await fake_embed_fn(texts)

    strategy = StructureSemanticChunking(similarity_threshold=0.99, max_chunk_tokens=MAX_TOKENS)
    await strategy.chunk(_heading_less_document(), counting_embed_fn)

    assert len(calls) == 1


async def test_semantic_chunking_keeps_embeddings_for_unmerged_candidates():
    """Reused by the pipeline so the corpus is not embedded twice at full cost."""
    blocks = [
        Block(text="Heading A", block_type="heading", section="A"),
        Block(text="topic-a body.", block_type="paragraph"),
        Block(text="Heading B", block_type="heading", section="B"),
        Block(text="topic-b body.", block_type="paragraph"),
    ]
    strategy = StructureSemanticChunking(similarity_threshold=0.99, max_chunk_tokens=1000)

    candidates = await strategy.chunk(blocks, fake_embed_fn)
    assert all(c.embedding is not None for c in candidates)


async def test_semantic_chunking_clears_the_embedding_of_a_merged_candidate():
    blocks = [
        Block(text="Heading", block_type="heading", section="Heading"),
        Block(text="topic-a one.", block_type="paragraph"),
        Block(text="topic-a two.", block_type="paragraph"),
    ]
    strategy = StructureSemanticChunking(similarity_threshold=0.5, max_chunk_tokens=1000)

    [candidate] = await strategy.chunk(blocks, fake_embed_fn)
    assert candidate.embedding is None  # merged text differs; must be re-embedded


def test_strategy_factory_honours_the_setting():
    from app.core.config import Settings

    assert isinstance(
        get_chunking_strategy(Settings(chunking_strategy="semantic")), StructureSemanticChunking
    )
    assert isinstance(get_chunking_strategy(Settings(chunking_strategy="fixed")), FixedChunking)
    with pytest.raises(ValueError):
        get_chunking_strategy(Settings(chunking_strategy="nonsense"))
