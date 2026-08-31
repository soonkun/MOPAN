"""Neighbour-chunk expansion: the retrieved chunk plus the adjacent chunk that
qualifies it.

The failure this exists for, in the owner's words: a chunk ends at "~~~가
가능하다" and the very next chunk opens "다만, ~~~의 경우에는 예외를 허용한다".
Retrieval returns the rule, the exception never reaches the model, and the answer
is confidently wrong. Measured on the live corpus (2578 chunks of 특허·실용신안
심사기준.pdf at CHUNK_SIZE=500 / CHUNK_OVERLAP=150): 149 of 2577 adjacent pairs
(5.8%) are a rule chunk followed by a chunk whose own body opens with a proviso
marker, and 27 chunks open with a dangling reference.

THE OVERLAP ALREADY COVERS ONE DIRECTION AND NOT THE OTHER, which is the whole
reason `targeted` is shaped the way it is. CHUNK_OVERLAP=150 repeats the previous
chunk's tail at the head of the next one - measured, 2242 of 2577 real pairs carry
such a prefix, 76-151 characters, mean 136 - so a chunk that opens mid-thought
already arrives with its lead-in. Nothing carries the other way: the chunk that
states the rule has no idea a proviso follows it. Forward is the gap.
"""

import re
import uuid
from typing import Literal

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import count_tokens
from app.models.chunk import Chunk
from app.retrieval.evidence import RetrievedChunk

ExpansionMode = Literal["off", "targeted", "blanket"]

# What a chunk that qualifies the one before it opens with. Korean legal drafting
# is formulaic here, which is what makes a marker list a usable signal at all:
# over the 2577 adjacent pairs of the live corpus these five match 149 chunks,
# 104 of them "다만". "단," carries its comma on purpose - bare "단" is also the
# unit 단(段)/단락 and the stem of 단순히, and it fired on prose that qualifies
# nothing.
PROVISO_MARKERS = ("다만", "단,", "그러나", "예외적으로", "이 경우")

# What a chunk that cannot stand alone opens with - it is talking about text that
# is not in it. "위 " and "앞의 " keep their trailing space for the same reason
# "단," keeps its comma: 위원회 and 앞장 are not references.
DANGLING_MARKERS = ("이 경우", "위 ", "앞의 ")

_WHITESPACE = re.compile(r"\s+")

# The ONE thing build_prompt takes out of ANSWER_CONTEXT_TOKEN_BUDGET before the
# first evidence item is rendered: the evidence fence and its trailing reminder.
#
# The system prompt is NOT here, and its absence is the whole point. That budget
# now bounds evidence and history only - the prompt and the question are charged
# against build_prompt's own MANDATORY_TOKEN_ALLOWANCE, and app/prompts/router.py
# refuses to store a template over it - so below the allowance the prompt costs
# the evidence budget nothing and reserving for it only makes expansion
# under-spend. An earlier version of this line carried the prompt's token count
# as a literal and was wrong within a day of someone editing the prose; the
# lesson is not "use a fresher number", it is that a number derived from an
# editable template does not belong in a constant at all.
#
# The fence does belong: it is a fixed string in the security design, not prose
# anybody edits from a screen, and expansion cannot read it - app.retrieval must
# not import app.chat. So it is pinned HERE and derived THERE:
# tests/test_neighbors.py recomputes it from `_fence` itself and fails the moment
# it moves. Same for the allowance the prompt is assumed to fit inside.
# build_prompt, not this module, remains the authority on what actually fits.
# 71, not 59, and this is a BUG FIX rather than a tuning change. The fence
# carries `new_nonce()` TWICE, and a nonce is 16 random uppercase hex characters:
# how many tokens that is depends on the draw. Measured over 5000 nonces the
# charge ranges 49-69 with a median of 57, so a reserve of 59 was under the real
# charge on about one request in five - and
# tests/test_neighbors.py:test_the_fence_reserve_still_matches_what_build_prompt_charges
# recomputes it from a FRESH nonce, so that test failed at the same rate. It was
# flaky, not stale, which is why re-running it made the failure go away.
#
# 71 is the arithmetic upper bound rather than the largest number anybody
# happened to observe: the fence without a nonce is 39 tokens, and the worst case
# for two 16-character nonces is one token per character, 39 + 32. A reserve is
# meant to be an upper bound; a percentile is what put an evidence item off the
# end of the prompt on the unlucky draw.
FENCE_RESERVE_TOKENS = 71

# One evidence item's "[n] (filename, p.593, section)\n" label (measured at 40 on
# a real citation of this corpus) plus the "\n\n" that joins it to the previous
# item (1).
PER_ITEM_OVERHEAD_TOKENS = 41


def _normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def strip_overlap(previous: str, following: str, overlap_chars: int) -> str | None:
    """`following` with the repeated tail of `previous` removed - or None when it
    carries no such repeat.

    None IS THE HEADING TEST, not a "nothing to do". build_size_bounded_candidates
    prefixes the previous chunk's tail onto the next one ONLY when the size bound
    forced the cut; a chunk opened by a heading starts clean, because a heading is
    a boundary the document itself drew. So "carries an overlap prefix" and "was
    split by size, mid-thought" are the same fact, and this function returning
    None is the caller's signal to stop rather than join across a section break.
    Measured on the live corpus: 335 of 2577 pairs have no prefix and 332 of those
    (99.1%) also change section, while 614 of the 2242 that DO have one change
    section as well - so the recorded `section` is the noisier of the two signals
    and is used only as the fallback below. Not one of the 149 proviso pairs is
    lost to this rule: all 149 carry a prefix.

    The comparison is whitespace-normalised because the prefix is not always a
    verbatim slice: `_sentence_aligned_tail` rejoins the tail's sentences with a
    single space, so the newlines inside it do not survive. The scan takes the
    LAST newline within the overlap window whose head matches, which is the end of
    the prefix even when the prefix contains newlines of its own.
    """
    if overlap_chars <= 0:
        return None
    tail = _normalise(previous)
    cut = 0
    # +2 for the newline the builder inserts between prefix and body, and for a
    # prefix that measures one character over its window.
    for match in re.finditer("\n", following[: overlap_chars + 2]):
        head = _normalise(following[: match.start()])
        if head and tail.endswith(head):
            cut = match.end()
    return following[cut:] if cut else None


def opens_with(text: str, markers: tuple[str, ...]) -> str | None:
    head = text.lstrip()
    return next((marker for marker in markers if head.startswith(marker)), None)


def _joined_body(previous, following, overlap_chars: int) -> str | None:
    """`following`'s own text if it may be joined to `previous`, else None.

    Anything with `.content` and `.section` will do, which is what lets a
    RetrievedChunk and a Chunk row be passed interchangeably.
    """
    body = strip_overlap(previous.content, following.content, overlap_chars)
    if body is not None:
        return body
    # CHUNK_OVERLAP=0 is reachable from the admin settings screen, and it leaves
    # no prefix to detect - which would turn expansion into a silent no-op for
    # whoever set it. The recorded section is the only signal left; it is noisier
    # (see strip_overlap) and is why it is the fallback rather than the test.
    if overlap_chars <= 0 and previous.section == following.section:
        return following.content
    return None


async def _load_neighbours(db: AsyncSession, wanted: set[tuple[uuid.UUID, int]]) -> dict:
    if not wanted:
        return {}
    rows = (
        await db.execute(
            select(
                Chunk.id,
                Chunk.document_id,
                Chunk.chunk_index,
                Chunk.content,
                Chunk.page,
                Chunk.section,
            ).where(tuple_(Chunk.document_id, Chunk.chunk_index).in_(sorted(wanted)))
        )
    ).all()
    return {(row.document_id, row.chunk_index): row for row in rows}


async def expand(
    db: AsyncSession,
    selected: list[RetrievedChunk],
    *,
    mode: ExpansionMode,
    overlap_chars: int,
    token_budget: int,
    query: str,
) -> None:
    """Enlarge each selected chunk with its neighbours, in place.

    IDENTITY IS UNCHANGED AND THAT IS THE POINT: `chunk_id`, `document_id`, `page`
    and `section` still name the PRIMARY chunk, so an expanded item is still one
    citation at the same position in the numbering. Only `content` grows, and
    `neighbors` records what was folded in so the trace screen can say so.

    Modes:
      off       - returns immediately. Byte for byte the behaviour before this
                  existed.
      targeted  - joins the next chunk when its body opens with a proviso marker,
                  and the previous chunk when THIS chunk's body opens with a
                  dangling reference. Cheap, and it fires on exactly the pairs
                  the failure was measured on.
      blanket   - joins both neighbours whenever the boundary permits it.

    Radius is fixed at one. The measured failure is a rule and the sentence that
    immediately qualifies it; +/-2 doubles the cost of the mode that was already
    the expensive one and there is no measurement asking for it.

    THE BUDGET IS A CEILING ON THE WHOLE EVIDENCE SET, not on one item. The
    running total starts at what every selected chunk costs UNEXPANDED plus what
    build_prompt charges around them, and an expansion is applied only if the
    total still fits. That is what makes the guarantee testable: with expansion on,
    every item that would have reached the model with it off still reaches it -
    expansion can only lose its own additions, never someone else's chunk.
    """
    if mode == "off" or not selected:
        return

    wanted = {
        (uuid.UUID(chunk.document_id), index)
        for chunk in selected
        if chunk.chunk_index is not None
        for index in (chunk.chunk_index - 1, chunk.chunk_index + 1)
        if index >= 0
    }
    # The key is (document_id, chunk_index), so a neighbour from ANOTHER document
    # cannot be selected: crossing a document boundary is not prevented by a
    # check here, it is unrepresentable.
    neighbours = await _load_neighbours(db, wanted)

    total = (
        FENCE_RESERVE_TOKENS
        # The question's own tokens are free up to build_prompt's mandatory
        # allowance and come out of THIS budget past it. Counting them is the
        # cheap upper bound on that overrun, and the only part of it expansion
        # can see: an 8000-character question is reachable from ChatRequest.
        + count_tokens(query)
        + PER_ITEM_OVERHEAD_TOKENS * len(selected)
        + sum(count_tokens(chunk.content) for chunk in selected)
    )

    for chunk in selected:
        if chunk.chunk_index is None:
            continue
        document_id = uuid.UUID(chunk.document_id)
        previous = neighbours.get((document_id, chunk.chunk_index - 1))
        following = neighbours.get((document_id, chunk.chunk_index + 1))

        parts: list[str] = [chunk.content]
        merged: list[dict] = []

        if previous is not None:
            own_body = _joined_body(previous, chunk, overlap_chars)
            if own_body is not None:
                reason = "blanket" if mode == "blanket" else opens_with(own_body, DANGLING_MARKERS)
                if reason:
                    parts[0] = own_body
                    parts.insert(0, previous.content)
                    merged.append(_record(previous, -1, reason, previous.content))

        if following is not None:
            next_body = _joined_body(chunk, following, overlap_chars)
            if next_body is not None:
                reason = "blanket" if mode == "blanket" else opens_with(next_body, PROVISO_MARKERS)
                if reason:
                    parts.append(next_body)
                    merged.append(_record(following, 1, reason, next_body))

        if not merged:
            continue
        content = "\n".join(parts)
        cost = count_tokens(content) - count_tokens(chunk.content)
        if total + cost > token_budget:
            # Not a break: a later item's expansion can still be small enough to
            # fit, and skipping it would make the budget depend on rank rather
            # than on size.
            continue
        total += cost
        chunk.content = content
        chunk.neighbors = merged


def _record(row, offset: int, reason: str, text: str) -> dict:
    return {
        "chunk_id": str(row.id),
        "chunk_index": row.chunk_index,
        "offset": offset,
        "page": row.page,
        "reason": reason,
        "tokens": count_tokens(text),
    }
