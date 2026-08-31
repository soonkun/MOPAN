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


def test_size_pass_does_not_orphan_a_heading_that_is_followed_by_a_heading():
    """Every `# Title` / `## Section` document hit this: the title opened a
    candidate, the next heading opened another, and the title shipped as a chunk
    of its own - 12 tokens and 10 characters on the markdown file in the dev
    corpus, an embedding call and an index entry for a string that answers
    nothing. The title has to ride along with the first body it introduces, and
    the citation has to name that body's section, not the title."""
    blocks = [
        Block(text="농약 안전사용 지침", block_type="heading", section="농약 안전사용 지침"),
        Block(text="1. 보관 기준", block_type="heading", page=4, section="1. 보관 기준"),
        Block(text="농약은 서늘한 곳에 보관한다.", block_type="paragraph", page=4, section="1. 보관 기준"),
        Block(text="2. 희석 배수", block_type="heading", page=5, section="2. 희석 배수"),
        Block(text="라벨의 값을 따른다.", block_type="paragraph", page=5, section="2. 희석 배수"),
    ]
    candidates = build_size_bounded_candidates(blocks, 1000)

    assert [c.content for c in candidates] == [
        "농약 안전사용 지침\n1. 보관 기준\n농약은 서늘한 곳에 보관한다.",
        "2. 희석 배수\n라벨의 값을 따른다.",
    ]
    assert [(c.page, c.section) for c in candidates] == [(4, "1. 보관 기준"), (5, "2. 희석 배수")]


def test_size_pass_does_not_orphan_a_heading_that_a_long_body_would_push_over():
    """The heading-then-heading fix had a hole its own test could not reach: it
    ran at max_tokens=1000, where nothing is ever over the limit. When the body
    that follows a heading is long enough that heading + first piece exceeds the
    limit, `over_limit` fires BEFORE the heading_only guard is consulted and the
    heading ships alone anyway. Measured on the unfixed code: a heading plus a
    40-sentence paragraph under max=200 gave token counts [4, 196, 196, 168] -
    the 4 is the orphan. Sweeping body length against token limit, 350 of 1330
    combinations reproduced it. The body's first piece has to be split against
    what is LEFT after the heading, not against the whole limit."""
    body = " ".join(f"This is sentence number {i} of the body." for i in range(40))
    blocks = [
        Block(text="1. Dilution", block_type="heading", page=1, section="1. Dilution"),
        Block(text=body, block_type="paragraph", page=1, section="1. Dilution"),
    ]
    candidates = build_size_bounded_candidates(blocks, 200)

    assert candidates[0].content.startswith("1. Dilution\n"), (
        f"heading orphaned: first candidate is {candidates[0].content!r}"
    )
    # The absorb must not be bought by busting the bound it exists under.
    assert all(c.token_count <= 200 for c in candidates), [c.token_count for c in candidates]


def test_size_pass_still_bounds_a_run_of_headings():
    """Absorbing forward must not become a way past the token limit: a document
    that is nothing but headings still has to come out under it."""
    blocks = [Block(text=f"Heading number {i} of many.", block_type="heading") for i in range(40)]
    candidates = build_size_bounded_candidates(blocks, 20)

    assert len(candidates) > 1
    for candidate in candidates:
        assert count_tokens(candidate.content) <= candidate.token_count <= 20


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


def test_fixed_chunking_rejects_a_non_positive_token_limit():
    # Settings blocks 0, but FixedChunking is also constructed directly (Task 13's
    # pipeline, the comparison view). Without this the failure surfaces as
    # "max_tokens must be at least 1" from inside chunk(), mid-document.
    with pytest.raises(ValueError, match="max_chunk_tokens"):
        FixedChunking(chunk_size=100, overlap=0, max_chunk_tokens=0)


async def test_fixed_chunking_splits_by_size_with_overlap():
    """No-re-split regime: 400 ASCII characters is ~100 cl100k tokens against the
    500-token default, so MAX_CHUNK_TOKENS never bites and each emitted chunk IS
    the verbatim window. Only here does the seam between adjacent chunks equal
    the configured overlap; the re-split regime is covered by the next test."""
    # Distinct characters, so the overlap assertion below cannot pass by accident
    # on a run of identical ones.
    text = "".join(chr(ord("a") + i % 26) for i in range(1000))
    blocks = [Block(text=text, block_type="paragraph", page=7, section="S")]
    candidates = await FixedChunking(chunk_size=400, overlap=50).chunk(blocks, fake_embed_fn)

    assert len(candidates) > 1
    assert all(c.char_count <= 400 for c in candidates)
    # The user asked for configurable size AND overlap. Without this the overlap
    # value is free to be ignored entirely and every size assertion still passes.
    assert candidates[0].content[-50:] == candidates[1].content[:50]


async def test_fixed_chunking_bounds_windows_by_tokens_not_characters():
    """chunk_size counts characters; the embedding ceiling counts tokens, and the
    ratio is script-dependent. At the shipped defaults a Korean document produced
    1142-token windows against a 500-token limit."""
    blocks = [Block(text="가나다라마바사아자차카타파하" * 100, block_type="paragraph")]
    candidates = await FixedChunking(chunk_size=800, overlap=100, max_chunk_tokens=60).chunk(
        blocks, fake_embed_fn
    )

    assert candidates
    assert all(count_tokens(c.content) <= 60 for c in candidates)


async def test_fixed_chunking_loses_no_source_text_in_the_re_split_regime():
    """Re-split regime: Korean at the shipped defaults, where 800 characters is
    ~1140 tokens against the 500-token limit, so every window is re-split.

    Overlap no longer produces a shared seam between adjacent emitted chunks
    here - the parts are re-splits of the window, not slices of it, so measuring
    `content[-overlap:] == next.content[:overlap]` is simply false (2 of 6
    adjacent pairs at these defaults). What overlap still guarantees is the thing
    it exists for: nothing falls into a gap between two windows, so every source
    character still appears, in order, across the emitted chunks."""
    source = "가나다라마바사아자차카타파하" * 200
    blocks = [Block(text=source, block_type="paragraph")]
    candidates = await FixedChunking(chunk_size=800, overlap=100, max_chunk_tokens=500).chunk(
        blocks, fake_embed_fn
    )

    assert all(count_tokens(c.content) <= 500 for c in candidates)
    window_count = -(-len(source) // (800 - 100))
    assert len(candidates) > window_count, "re-splitting never fired; wrong regime"

    emitted = "".join(c.content for c in candidates)
    position = 0
    for index, character in enumerate(source):
        found = emitted.find(character, position)
        assert found >= 0, f"source character {index} was dropped between windows"
        position = found + 1


async def test_fixed_chunking_attributes_each_part_to_the_block_it_came_from():
    """A window spans block boundaries, so the window-start block's page/section
    is the wrong citation for a part drawn from a later block. With re-splitting
    active a single 2000-character window covers this whole document, and every
    part - including the ones that contain only block two's text - was cited as
    page 1 of "First"."""
    blocks = [
        Block(text="alpha. " * 60, block_type="paragraph", page=1, section="First"),
        Block(text="omega. " * 60, block_type="paragraph", page=9, section="Second"),
    ]
    candidates = await FixedChunking(chunk_size=2000, overlap=0, max_chunk_tokens=60).chunk(
        blocks, fake_embed_fn
    )

    pure_second = [c for c in candidates if "alpha" not in c.content]
    assert pure_second, "fixture no longer produces a part drawn only from block two"
    assert all((c.page, c.section) == (9, "Second") for c in pure_second)
    assert candidates[0].page == 1 and candidates[0].section == "First"


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        (FixedChunking(chunk_size=400, overlap=0), "fixed"),
        (StructureSemanticChunking(similarity_threshold=0.5, max_chunk_tokens=1000), "semantic"),
    ],
)
async def test_every_candidate_is_tagged_with_its_strategy(strategy, expected):
    """The document detail view compares strategies side by side, so an untagged
    candidate cannot be attributed. Covers the single-candidate document too,
    where the semantic pass returns before the merge loop."""
    blocks = [Block(text="topic-a sentence one.", block_type="paragraph")]
    single = await strategy.chunk(blocks, fake_embed_fn)
    assert [c.metadata["strategy"] for c in single] == [expected]

    many = await strategy.chunk(
        [Block(text="topic-a sentence.", block_type="paragraph") for _ in range(40)], fake_embed_fn
    )
    assert all(c.metadata["strategy"] == expected for c in many)


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

    # A collection that configures nothing gets the admin's prose strategy and
    # nothing wrapped around it. The table cutter is opted INTO, never sniffed.
    assert isinstance(
        get_chunking_strategy(Settings(chunking_strategy="semantic")),
        StructureSemanticChunking,
    )
    assert isinstance(get_chunking_strategy(Settings(chunking_strategy="fixed")), FixedChunking)
    with pytest.raises(ValueError):
        get_chunking_strategy(Settings(chunking_strategy="nonsense"))


def test_a_collection_selects_the_table_cutter_and_a_typo_is_reported():
    from app.core.config import Settings
    from app.rag.chunking.table import ClassificationTableChunking

    settings = Settings(chunking_strategy="semantic")
    chosen = get_chunking_strategy(
        settings, {"strategy": "classification_table", "preset": "korean_ip_classification"}
    )
    assert isinstance(chosen, ClassificationTableChunking)
    # The prose strategy is NOT consulted for a collection that chose this one.
    assert not hasattr(chosen, "fallback")

    # Silence is the one thing a misconfiguration must not buy.
    with pytest.raises(ValueError):
        get_chunking_strategy(settings, {"strategy": "clasification_table"})
    with pytest.raises(ValueError):
        get_chunking_strategy(settings, {"strategy": "classification_table", "preset": "nope"})
    with pytest.raises(ValueError):
        get_chunking_strategy(settings, {"strategy": "classification_table", "marker": "([a-z]+"})
    with pytest.raises(ValueError):
        get_chunking_strategy(settings, {"strategy": "classification_table"})


# --- Character target and overlap --------------------------------------------

TARGET_CHARS = 300
OVERLAP_CHARS = 60


def _sentence_body(sentence_count: int = 60) -> str:
    return " ".join(f"Sentence number {i} runs on for a little while here." for i in range(sentence_count))


def _korean_body(sentence_count: int = 60) -> str:
    sentence = "제{}항의 농약은 라벨에 적힌 희석 배수를 지켜 사용하여야 한다."
    return " ".join(sentence.format(i) for i in range(sentence_count))


def test_size_pass_lands_chunks_on_the_character_target():
    """MAX_CHUNK_TOKENS alone does not produce ~1000-character chunks - the token
    bound bites first and the character size follows the script. Measured on the
    real 854-page Korean manual at MAX_CHUNK_TOKENS=500: chunks cut at ~549
    characters and averaged 362. The character target is what puts them on size."""
    blocks = [Block(text=_sentence_body(), block_type="paragraph", page=1)]

    candidates = build_size_bounded_candidates(blocks, 10_000, TARGET_CHARS, OVERLAP_CHARS)

    assert len(candidates) > 1, "the character target never fired"
    assert all(c.char_count <= TARGET_CHARS for c in candidates), [c.char_count for c in candidates]
    # Aiming at the target, not merely under it: a splitter that cut anywhere below
    # it would pass the bound above and still ship 100-character chunks.
    body = [c.char_count for c in candidates[:-1]]
    assert min(body) > TARGET_CHARS // 2, body


def test_size_pass_keeps_the_token_ceiling_as_the_hard_bound():
    """The character target is a target; the token count is the guarantee, because
    it is what protects the embedding input limit. Korean measures up to 1.213
    cl100k tokens per character, so a character target alone bounds nothing."""
    blocks = [Block(text=_korean_body(), block_type="paragraph", page=1)]

    candidates = build_size_bounded_candidates(blocks, MAX_TOKENS, 10_000, OVERLAP_CHARS)

    assert len(candidates) > 1
    for candidate in candidates:
        assert count_tokens(candidate.content) <= candidate.token_count <= MAX_TOKENS


def test_size_pass_repeats_the_previous_tail_after_a_size_split():
    """A chunk that starts exactly where the previous one stopped loses whatever
    straddles the seam. Every cut the SIZE bound forced carries the tail across."""
    blocks = [Block(text=_sentence_body(), block_type="paragraph", page=1)]

    candidates = build_size_bounded_candidates(blocks, 10_000, TARGET_CHARS, OVERLAP_CHARS)

    assert len(candidates) > 2
    for previous, candidate in zip(candidates, candidates[1:], strict=False):
        head = candidate.content.split("\n", 1)[0]
        assert previous.content.endswith(head), (previous.content[-80:], head)
        assert OVERLAP_CHARS // 2 <= len(head) <= OVERLAP_CHARS


def test_size_pass_starts_a_heading_chunk_clean():
    """A heading is a boundary the DOCUMENT drew. Carrying the previous section's
    tail across it puts text in a chunk the author placed in another section and
    makes the embedding describe both, so overlap belongs only to a size split."""
    blocks = [
        Block(text="1. 보관", block_type="heading", page=1, section="1. 보관"),
        Block(text=_sentence_body(), block_type="paragraph", page=1, section="1. 보관"),
        Block(text="2. 희석", block_type="heading", page=2, section="2. 희석"),
        Block(text="라벨의 값을 따른다.", block_type="paragraph", page=2, section="2. 희석"),
    ]

    candidates = build_size_bounded_candidates(blocks, 10_000, TARGET_CHARS, OVERLAP_CHARS)

    opened_at_heading = [c for c in candidates if c.content.startswith("2. 희석")]
    assert len(opened_at_heading) == 1, [c.content[:40] for c in candidates]
    assert opened_at_heading[0].content == "2. 희석\n라벨의 값을 따른다."


def test_size_pass_cuts_the_overlap_on_a_sentence_boundary():
    """A fixed-width tail opens mid-word, which is noise in the chunk text and in
    its embedding alike. split_sentences already knows this corpus's terminators."""
    blocks = [Block(text=_sentence_body(), block_type="paragraph", page=1)]

    candidates = build_size_bounded_candidates(blocks, 10_000, TARGET_CHARS, OVERLAP_CHARS)

    first, second = candidates[0], candidates[1]
    head = second.content.split("\n", 1)[0]
    assert head != first.content[-OVERLAP_CHARS:], "overlap is the raw character tail"
    # Whole sentences of the previous chunk, in order, starting at one of them.
    assert head.startswith("Sentence number "), head
    assert split_sentences(head) == [s for s in split_sentences(first.content) if s in head]


@pytest.mark.parametrize("overlap", [0, 30, 60])
def test_size_pass_does_not_orphan_a_heading_under_the_character_target(overlap):
    """The token bound's orphan hole has a character twin: if the body's first
    piece is split against the whole target, heading + piece exceeds it,
    `over_target` fires, and the heading ships alone. The overlap sweep is what
    reaches it - at overlap 60 the body is already split against 239 of the 300
    and the heading fits in the slack by luck, so a single-configuration test
    passes against the defect."""
    # A heading long enough that heading + the body's first piece runs past the
    # target: at 300/60 the pieces come out at 254 characters, so a short heading
    # fits in the slack and the defect hides. Section titles this long are normal
    # in the manual this was measured on.
    heading = "1. Dilution rates, mixing order and protective equipment"
    blocks = [
        Block(text=heading, block_type="heading", page=1, section=heading),
        Block(text=_sentence_body(), block_type="paragraph", page=1, section=heading),
    ]

    candidates = build_size_bounded_candidates(blocks, 10_000, TARGET_CHARS, overlap)

    assert candidates[0].content.startswith(f"{heading}\n"), (
        f"heading orphaned: first candidate is {candidates[0].content!r}"
    )
    assert all(c.char_count <= TARGET_CHARS for c in candidates), [c.char_count for c in candidates]


def test_size_pass_drops_the_overlap_rather_than_bust_the_token_ceiling():
    """The target may be missed; the ceiling may not. A limit too small to hold
    the overlap AND content ships without the overlap."""
    blocks = [Block(text=_korean_body(), block_type="paragraph", page=1)]

    candidates = build_size_bounded_candidates(blocks, 40, TARGET_CHARS, OVERLAP_CHARS)

    assert len(candidates) > 1
    assert all(c.token_count <= 40 for c in candidates), [c.token_count for c in candidates]


async def test_semantic_merge_respects_the_character_target():
    """Pass 2 must stay the exact negation of pass 1's split predicate on BOTH
    bounds. Checking only the token ceiling lets the merge rejoin a pair the
    character target split and ship a chunk at twice the target size."""
    blocks = [
        Block(text="A", block_type="heading", section="A"),
        Block(text="topic-a " + "x" * 60, block_type="paragraph", section="A"),
        Block(text="B", block_type="heading", section="B"),
        Block(text="topic-a " + "y" * 60, block_type="paragraph", section="B"),
    ]
    strategy = StructureSemanticChunking(
        similarity_threshold=0.5, max_chunk_tokens=1000, target_chars=100, overlap_chars=20
    )

    candidates = await strategy.chunk(blocks, fake_embed_fn)

    assert len(candidates) == 2, [c.content for c in candidates]
    assert all(c.char_count <= 100 for c in candidates), [c.char_count for c in candidates]


# --- classification table ----------------------------------------------------

from app.rag.chunking.table import (  # noqa: E402
    PRESETS,
    ClassificationTableChunking,
    resolve,
)

KOREAN_IP = {"strategy": "classification_table", "preset": "korean_ip_classification"}


def _table_strategy(**kwargs) -> ClassificationTableChunking:
    return ClassificationTableChunking(resolve(KOREAN_IP), **kwargs)


def _goods_table(sections: int = 25, goods_per_section: int = 12) -> list[Block]:
    """The shape 유사상품 심사기준 has: a bracketed class/similarity-group header,
    then a scope line, then a long run of goods names with nothing in them that
    says which class they belong to."""
    blocks: list[Block] = []
    for i in range(sections):
        blocks.append(
            Block(text=f"[제9류/G39{i:04d}] 소프트웨어{i}", block_type="heading", page=i, section="9류")
        )
        blocks.append(
            Block(text=f"◦ 상품{i}의 범위", block_type="paragraph", page=i, section="9류")
        )
        for g in range(goods_per_section):
            blocks.append(
                Block(text=f"상품이름{i}-{g} goods name {i}-{g}", block_type="paragraph", page=i)
            )
    return blocks


def test_the_shipped_preset_matches_the_marker_shapes_the_document_really_has():
    """PRESETS entries are configuration, and configuration that does not match
    the documents it names is worth nothing. These four are the shapes measured in
    유사상품 심사기준."""
    marker = resolve(KOREAN_IP).marker
    assert marker.match("[제9류/G390802] 소프트웨어").group("class_no") == "9"
    assert marker.match("[제35류/S120602] 광고업").group("code") == "S120602"
    assert marker.match("[제9류/G390702, G390799] 무선 이어셋, 헤드셋").group("name") == (
        "무선 이어셋, 헤드셋"
    )
    assert marker.match("[제9류/G3902, G3903]").group("name") == ""
    # Prose that merely mentions a class is not a header.
    assert marker.match("제9류에 해당하는 상품은 다음과 같다.") is None


async def test_a_document_with_no_markers_is_size_bounded_not_lost():
    """Selecting this strategy for a collection whose document has no markers is
    the user's business; losing the text would be ours."""
    strategy = _table_strategy(max_chunk_tokens=1000, target_chars=400)

    candidates = await strategy.chunk(_heading_less_document(), fake_embed_fn)

    assert candidates
    assert "classification_table" not in {c.metadata.get("strategy") for c in candidates}


async def test_every_chunk_of_a_section_carries_its_class_and_group_code():
    """The failure this exists for: a goods chunk with no code in it cannot be
    retrieved by a question that names a class, and cannot ground an answer that
    names one either."""
    strategy = _table_strategy(max_chunk_tokens=1000, target_chars=200)

    candidates = await strategy.chunk(_goods_table(), fake_embed_fn)

    assert all("[제9류/G39" in c.content for c in candidates), [
        c.content[:40] for c in candidates if "[제9류/G39" not in c.content
    ]
    assert all(c.section.startswith("[제9류/G39") for c in candidates)
    assert all(c.metadata["strategy"] == "classification_table" for c in candidates)
    # More than one chunk per section, or the fixture is not exercising the
    # repetition this test is about.
    assert len(candidates) > 25


async def test_a_section_header_never_travels_without_its_goods():
    """The header used to land at the tail of the PREVIOUS section's chunk, so
    the section's own scope line was retrievable only as a footnote to somebody
    else's goods."""
    strategy = _table_strategy(max_chunk_tokens=1000, target_chars=400)

    candidates = await strategy.chunk(_goods_table(sections=25, goods_per_section=2), fake_embed_fn)

    first = next(c for c in candidates if "[제9류/G390001]" in c.content)
    assert "◦ 상품1의 범위" in first.content
    assert "상품0-" not in first.content, first.content


async def test_the_prefix_is_charged_to_both_size_bounds():
    strategy = _table_strategy(max_chunk_tokens=1000, target_chars=120)

    candidates = await strategy.chunk(_goods_table(), fake_embed_fn)

    assert all(c.char_count <= 120 for c in candidates), [c.char_count for c in candidates]
    assert all(c.char_count == len(c.content) for c in candidates)
    assert all(c.token_count >= count_tokens(c.content) for c in candidates)


async def test_a_class_preamble_is_not_stamped_with_the_previous_section_code():
    """A 니스 class opens with a preamble that carries no code of its own. Left
    inside the previous section it would ship a list of paints labelled as the
    last similarity group of the class before it - a wrong class code, which is
    worse here than no class code."""
    strategy = _table_strategy(max_chunk_tokens=1000, target_chars=400)
    blocks = [
        *_goods_table(sections=25, goods_per_section=2),
        Block(text="본류에는 주로 페인트, 착색제, 부식방지제가 포함된다.", block_type="paragraph", page=99),
        Block(text="- 공업용 페인트, 니스 및 래커", block_type="paragraph", page=99),
    ]

    candidates = await strategy.chunk(blocks, fake_embed_fn)

    preamble = [c for c in candidates if "페인트" in c.content]
    assert preamble, [c.content[:40] for c in candidates[-3:]]
    assert all("[제9류/" not in c.content for c in preamble)


async def test_every_chunk_opens_with_a_sentence_composed_from_its_own_marker():
    """The dense arm's whole complaint: a bracket of codes over 400 bare product
    names is not a sentence, so nothing sentence-shaped can be near it. Every
    fact in the line comes from the marker - nothing is invented and no model is
    called."""
    strategy = _table_strategy(max_chunk_tokens=1000, target_chars=300)

    candidates = await strategy.chunk(_goods_table(sections=25, goods_per_section=6), fake_embed_fn)
    table = [c for c in candidates if c.metadata.get("strategy") == "classification_table"]

    assert table
    for candidate in table:
        assert candidate.content.startswith("상표 출원 시 ")
    first = next(c for c in table if "[제9류/G390003]" in c.content)
    assert first.content.startswith(
        "상표 출원 시 소프트웨어3의 상품류는 제9류, 유사군코드는 G390003입니다."
    )
    # The citation still shows the marker, never the sentence.
    assert first.section == "[제9류/G390003] 소프트웨어3"


async def test_a_marker_with_nothing_to_say_gets_no_half_sentence():
    """A header that captured no name would otherwise ship "상품군 명칭은 입니다."
    on every chunk of its section."""
    strategy = _table_strategy(max_chunk_tokens=1000, target_chars=300)
    blocks = [
        Block(text="[제9류/G3902, G3903]", block_type="heading", page=1),
        Block(text="상품이름 goods name", block_type="paragraph", page=1),
    ]

    candidates = await strategy.chunk(blocks, fake_embed_fn)

    assert candidates[0].content.startswith("[제9류/G3902, G3903]")
    assert "명칭은 입니다" not in candidates[0].content


async def test_the_marker_and_the_sentence_are_configuration_not_korean():
    """The same strategy over a parts catalogue, with no preset and no Korean.
    If this needs a code change, the generalisation did not happen."""
    strategy = ClassificationTableChunking(
        resolve(
            {
                "strategy": "classification_table",
                "marker": r"^(?P<sku>[A-Z]{2}-\d{4})\s+(?P<name>.+)$",
                "head_line": "Part {sku} is a {name}.",
            }
        ),
        max_chunk_tokens=1000,
        target_chars=300,
    )
    blocks = [
        Block(text="AB-1201 hydraulic pump", block_type="heading", page=1),
        Block(text="Rated to 210 bar, cast iron body.", block_type="paragraph", page=1),
        Block(text="CD-7788 relief valve", block_type="heading", page=2),
        Block(text="Adjustable 20-250 bar.", block_type="paragraph", page=2),
    ]

    candidates = await strategy.chunk(blocks, fake_embed_fn)

    assert candidates[0].content.startswith("Part AB-1201 is a hydraulic pump.")
    assert candidates[0].section == "AB-1201 hydraulic pump"
    assert candidates[1].content.startswith("Part CD-7788 is a relief valve.")


def test_a_preset_is_a_starting_point_not_a_cage():
    """Take the Korean-IP marker, keep it, replace only the sentence."""
    markers = resolve({**KOREAN_IP, "head_line": "{class_no}류 {code}."})
    assert markers.marker.pattern == PRESETS["korean_ip_classification"]["marker"]
    assert markers.head_line == "{class_no}류 {code}."

