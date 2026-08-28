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
    Measured against the pre-fix splitter, that corrupted this text at 378 of 512
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
