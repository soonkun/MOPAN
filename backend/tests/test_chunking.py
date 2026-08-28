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
    Measured against the pre-fix splitter, that corrupted this text at 58 of 64
    max_tokens values - silent data loss in the language this system targets."""
    text = "가나다라마바사아자차카타파하" * 40
    pieces = split_to_token_limit(text, MAX_TOKENS)

    assert len(pieces) > 1
    assert all(count_tokens(p) <= MAX_TOKENS for p in pieces)
    assert "".join(pieces) == text
    assert not any("�" in p for p in pieces)


def test_split_to_token_limit_bounds_oversized_whitespace():
    """split_sentences drops whitespace-only fragments, so a whitespace-heavy
    block leaves nothing to rejoin; the fallback must still be size-bounded."""
    pieces = split_to_token_limit(" " * 4000, MAX_TOKENS)
    assert all(count_tokens(p) <= MAX_TOKENS for p in pieces)


def test_split_to_token_limit_rejects_a_non_positive_limit():
    # max_chunk_tokens is an operator-facing setting, so 0 is reachable from
    # configuration; fail with a named cause, not `range() arg 3 must not be zero`.
    with pytest.raises(ValueError):
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
    counts does sit below it - the joining separator is a token the sum omits."""
    for candidate in build_size_bounded_candidates(_heading_less_document(), MAX_TOKENS):
        assert count_tokens(candidate.content) <= candidate.token_count <= MAX_TOKENS


def test_size_pass_never_exceeds_the_limit_even_for_one_huge_block():
    blocks = [Block(text="Sentence about blight. " * 400, block_type="paragraph")]
    candidates = build_size_bounded_candidates(blocks, MAX_TOKENS)
    assert len(candidates) > 1
    assert all(count_tokens(c.content) <= MAX_TOKENS for c in candidates)


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
