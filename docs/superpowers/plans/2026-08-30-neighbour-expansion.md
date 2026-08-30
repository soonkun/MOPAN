# MOPAN — Neighbour-chunk expansion — Implementation Plan

> **Scope:** retrieval only. This document does not amend any other plan; the files it modifies outside `backend/app/retrieval/` are one call site, one settings field, one trace field and the eval harness.

**The problem, in the owner's words:**

> 청크가 "~~~가 가능하다" 까지만 가져온다고 해봐. 근데 그 뒤에 "단, ~~~의 경우에는 예외를 허용한다" 라던지. 가져온 청크가 "1, 2에서 제시된 내용 중 ~~" 이런 식으로 시작되어버리면 그 앞 내용이 뭔지 모르고 그냥 답해버릴 거 아니야.

Measured on the live corpus (2578 chunks of `특허·실용신안 심사기준.pdf` at `CHUNK_SIZE=500` / `CHUNK_OVERLAP=150`): 149 of 2577 adjacent pairs are a chunk followed by one whose own body opens with 다만 / 단, / 그러나 / 예외적으로 / 이 경우, and 27 chunks open with a dangling reference. **Neighbour expansion did not exist before this plan** — `grep -rn "neighbo\|adjacent" backend/app/retrieval/` returned nothing.

**What ships:**
- `backend/app/retrieval/neighbors.py` — overlap-aware joining, the boundary rules, the marker sets, and the budget arithmetic.
- Three defaulted keyword arguments on `hybrid_search`, opted into at the one choke point every direct-RAG caller reaches (`app/chat/service.py:retrieve`).
- `NEIGHBOR_EXPANSION`, an env-only setting with three values, defaulting to `targeted` on measurement.
- `Evidence.metadata["neighbors"]` → `messages.trace` → one line per merged neighbour on the trace screen.
- `--expansion` in `scripts/eval_retrieval.py`, and 8 new questions carrying `"group": "neighbor"`.

## Decisions

**The overlap prefix IS the heading test.** `build_size_bounded_candidates` carries `CHUNK_OVERLAP` characters of the previous chunk onto the next one **only when the size bound forced the cut**; a heading-opened chunk starts clean, because a heading is a boundary the document itself drew. So "carries an overlap prefix" and "was split mid-thought" are the same fact, and one function answers both "what do I strip" and "may I join at all". Measured: 2242 of 2577 pairs carry a prefix; of the 335 that do not, 332 (99.1%) also change section, while 614 of the 2242 that DO carry one change section as well — so the recorded `section` is the noisier signal and is used only as the `CHUNK_OVERLAP=0` fallback. Not one of the 149 proviso pairs is lost to the rule: all 149 carry a prefix.

**Targeted, not blanket, and the numbers decided it.** anchor@N on the 21 original questions, `off` → `targeted` → `blanket`: 0.619/0.714/0.714 at top_n=4, 0.714/0.810/0.810 at 6, 0.762/0.857/0.905 at 8, 0.857/0.905/0.952 at 14. Blanket ties targeted at the two smallest operating points and leads by exactly one question of 21 above them, for +73% to +120% evidence tokens against targeted's +6%. At top_n=14 blanket is also budget-bound — 8.5 of 14 items expand before `ANSWER_CONTEXT_TOKEN_BUDGET` stops it — so it cannot deliver its own behaviour consistently. The one question blanket buys is q15, whose answer chunk opens `(예2) 【청구항 1】`: an example continuation no marker recognises, and the honest reading is that a marker list will not reach it.

**The budget binds expansion, not `top_n`.** The running total starts at what every selected chunk costs UNEXPANDED plus what `build_prompt` charges around them, and an expansion is applied only if the total still fits. So turning expansion on can never push an evidence item out of the answer — it can only lose its own additions. At the deployed `RETRIEVAL_TOP_N=14` / `ANSWER_CONTEXT_TOKEN_BUDGET=10400`, `off` spends 5,536 tokens and `targeted` 5,849, so nothing changes and neither knob needed to move.

**Identity does not change.** `ref`, `chunk_id`, `document_id`, `page` and `section` still name the PRIMARY chunk. An expanded item is one citation at the same position in the numbering; `neighbors` in the metadata is the only thing that says the text is wider than the chunk it cites.

**The reserve is one number and it is not the prompt's.** `ANSWER_CONTEXT_TOKEN_BUDGET` bounds evidence and history only — `build_prompt` charges the system prompt and the question against its own `MANDATORY_TOKEN_ALLOWANCE`, and `app/prompts/router.py` refuses to store a template over it — so below that allowance the prompt costs the evidence budget nothing and reserving for it only makes expansion under-spend. The first version of this constant carried the prompt's token count as a literal and was wrong within a day of someone editing the prose. The lesson is not "use a fresher number": a number derived from an editable template does not belong in a constant. What is left is the evidence fence, a fixed string in the security design, pinned here because `app.retrieval` must not import `app.chat` — and re-derived from `_fence` itself in `tests/test_neighbors.py`, which fails the moment it moves. The allowance assumption has its own test for the same reason.

**Radius one.** The measured failure is a rule and the sentence that immediately qualifies it. ±2 doubles the cost of the mode that was already the expensive one and there is no measurement asking for it.

**What this does NOT fix, stated because it is the interesting negative.** Eight questions written from the measured proviso pairs (`"group": "neighbor"`) do not move under any mode. They do not need to: at `CHUNK_OVERLAP=150` the proviso chunk REPEATS the rule chunk's tail, so it is a near-duplicate in embedding space and retrieval already returns both — at top_n=6, 7 of the 8 already score anchor=1 with expansion off. The gain measured above is a different case: a chunk adjacent to a retrieved one that was itself outside the top N.

## Global Constraints

- `app.retrieval` must not import `app.chat`. `build_prompt` remains the authority on what fits; this module keeps a deliberately conservative reserve instead.
- One pytest session at a time, never `-n auto`.
- No test makes a real network call or a real OpenAI API call. `scripts/eval_retrieval.py` is a script, not a test, and its embeddings are cached on disk.
- Every user-facing `detail=`/label is natural Korean.

---

### Task 60: The expansion module

**Files:**
- Create: `backend/app/retrieval/neighbors.py`
- Modify: `backend/app/retrieval/evidence.py`

**Interfaces:**
- Produces: `expand`, `strip_overlap`, `opens_with`, `ExpansionMode`, `FENCE_RESERVE_TOKENS`, `PER_ITEM_OVERHEAD_TOKENS`, `RetrievedChunk.chunk_index`, `RetrievedChunk.neighbors`.
- Consumed by: `app/retrieval/service.py` (Task 61) and `scripts/eval_retrieval.py` (Task 63).

- [ ] **Step 1: Write `backend/app/retrieval/neighbors.py`**

```python
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
FENCE_RESERVE_TOKENS = 59

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
```

- [ ] **Step 2: Modify `backend/app/retrieval/evidence.py`**

Two fields on `RetrievedChunk`. `chunk_index` is optional because a Reranker or a hand-built item may have none, and expansion skips such an item rather than guessing an index.

```python
    # The chunk's position in its document, which is what neighbour expansion
    # addresses a neighbour BY. Optional because a Reranker or a test may build a
    # RetrievedChunk by hand; expansion skips an item that has none rather than
    # guessing an index.
    chunk_index: int | None = None
    # Per-stage scores kept separate. Collapsing them into one `score` means
    # Slice 5's trace view has to change the retrieval return type.
    vector_rank: int | None = None
    keyword_rank: int | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None
    # What neighbour expansion folded into `content`, one entry per neighbour.
    # Empty means this item is the stored chunk and nothing else - which is what
    # every item is when NEIGHBOR_EXPANSION is off.
    neighbors: list[dict] = field(default_factory=list)
```

And the one metadata key that says the content is wider than the chunk the item cites:

```python
            # The identity fields above all still name the PRIMARY chunk after
            # expansion; this is the only place that says the content is wider
            # than that chunk, and it is what the trace screen shows.
            "neighbors": chunk.neighbors,
```

---

#### Wiring, and the setting that turns it off

- [ ] **Step 3: Modify `backend/app/retrieval/service.py`**

Three defaulted keyword arguments, so every caller that says nothing keeps the behaviour it had:

```python
    neighbor_expansion: ExpansionMode = "off",
    chunk_overlap: int = 0,
    token_budget: int = 0,
) -> list[Evidence]:
    """Query -> (dense + sparse) -> RRF -> rerank -> top-N -> expand -> Evidence.

    RRF and the reranker are separate, separately configurable stages: RRF is
    arithmetic over two rank lists, the reranker is a model. Swapping in a
    cross-encoder means passing a different `Reranker`; nothing here changes.

    The last three arguments are neighbour expansion, and they DEFAULT TO OFF so
    that every caller that says nothing gets exactly the behaviour it got before
    expansion existed. The application wiring opts in, in chat/service.py, the
    same way it opts into `sparse_weight`. Expansion runs AFTER the reranker and
    after the top-N cut - a neighbour is not a candidate competing for a slot, it
    is text added to a slot that was already won - and BEFORE build_prompt's
    budget, which is why `token_budget` is passed rather than assumed.
    """
```

The call sits after the reranker and after the top-N cut, and before `build_prompt`'s budget:

```python
    # After the truncation, on the items that survived it. Expanding the whole
    # candidate set instead would pay for 20 neighbours to use 14, and expanding
    # BEFORE the rerank would let a neighbour's text change the score of the
    # chunk it was attached to.
    await expand(
        db,
        selected,
        mode=neighbor_expansion,
        overlap_chars=chunk_overlap,
        token_budget=token_budget,
        query=query,
    )
```

- [ ] **Step 4: Modify `backend/app/chat/service.py`**

```python
        # Neighbour expansion is opted into HERE, at the one choke point every
        # direct-RAG caller reaches - /api/chat, /api/search and the
        # orchestrator's fallback all come through this function - rather than at
        # four call sites that would each have to remember. CHUNK_OVERLAP is not
        # a chunking detail leaking into retrieval: it is how expansion knows
        # which repeated characters to drop when it joins two chunks.
        neighbor_expansion=settings.neighbor_expansion,
        chunk_overlap=settings.chunk_overlap,
        token_budget=settings.answer_context_token_budget,
```

- [ ] **Step 5: Modify `backend/app/core/config.py`**

`Literal`, so a typo is a boot failure and not a silently disabled feature. The measurement table lives here because this is where the default is chosen.

```python
    # Neighbour-chunk expansion. See app/retrieval/neighbors.py for the mechanism
    # and the corpus measurements; this note is the number that picked the
    # default.
    #
    # The failure: a chunk ends at "~~~가 가능하다" and the NEXT chunk opens
    # "다만, ~~~의 경우에는 예외를 허용한다". Retrieval returns the rule, the
    # exception never arrives, the answer is confidently wrong. On the live
    # 2578-chunk corpus 149 of 2577 adjacent pairs are exactly that shape.
    #
    # Measured on the real corpus with `python scripts/eval_retrieval.py
    # --variants current --expansion off,targeted,blanket [--top-n N]`, over the
    # 21 original questions in scripts/eval_questions_ko.json (group "base").
    # anchor@N is the metric that can see this at all - recall and precision are
    # page- and slot-level, and expansion adds text to slots already won, so
    # neither can move. `tokens` is the mean size of the whole evidence set.
    #
    #   top_n   off              targeted            blanket
    #            anchor  tokens   anchor  tokens      anchor  tokens
    #    4        0.619    1578    0.714    1665 +6%   0.714    3469 +120%
    #    6        0.714    2375    0.810    2509 +6%   0.810    5169 +118%
    #    8        0.762    3170    0.857    3357 +6%   0.905    6849 +116%
    #   10        0.762    3959    0.857    4200 +6%   0.905    8569 +116%
    #   14        0.857    5536    0.905    5849 +6%   0.952    9566  +73%
    #
    # TARGETED, because blanket buys one more question for ten times the tokens.
    # They tie at top_n 4 and 6; above that blanket leads by exactly one question
    # of 21 (q15, whose answer-bearing chunk opens "(예2) 【청구항 1】" - an example
    # continuation no marker recognises) and pays 3,500-3,900 tokens per answer
    # for it. At top_n=14 blanket is also BUDGET-BOUND: only 8.5 of 14 items get
    # expanded before ANSWER_CONTEXT_TOKEN_BUDGET stops it, so it cannot even
    # deliver its own behaviour consistently. Targeted expands 1.1 items of 14
    # and never comes near the budget.
    #
    # WHAT IT DOES NOT FIX, stated because it is the interesting negative. Eight
    # further questions (group "neighbor") were written from the measured
    # proviso pairs - the rule in one chunk, the 다만 that qualifies it in the
    # next. Expansion does not move them, because it does not need to: at
    # CHUNK_OVERLAP=150 the proviso chunk REPEATS the rule chunk's tail, so it is
    # a near-duplicate in embedding space and retrieval already returns both. At
    # top_n=6, 7 of those 8 already score anchor=1 with expansion off. The gain
    # measured above is a different case: a chunk adjacent to a retrieved one
    # that was itself outside the top N.
    #
    # Literal, not str: an operator's "targetted" would otherwise boot fine and
    # silently disable the feature they were switching on.
    neighbor_expansion: Literal["off", "targeted", "blanket"] = "targeted"
```

- [ ] **Step 6: Modify `backend/app/core/settings_store.py`**

`off`/`targeted`/`blanket` is a choice, not a number, and `SettingSpec` holds `int | float` — so it belongs beside the other values the 고급 설정 screen names and refuses to edit.

```python
        key="NEIGHBOR_EXPANSION",
        label="인접 청크 확장",
        reason=(
            "off / targeted / blanket 중 하나를 고르는 값이라 이 화면의 숫자 입력 칸으로는 다룰 수 "
            "없습니다. 답변에 쓰이는 근거의 분량을 바꾸므로 토큰 예산과 함께 판단해야 하며, "
            "scripts/eval_retrieval.py 로 측정한 뒤 환경변수로 바꿉니다."
        ),
```

- [ ] **Step 7: Modify `.env.example`**

```text
# Neighbour-chunk expansion: off | targeted | blanket. A retrieved chunk keeps
# its own citation and its own chunk id; the adjacent chunk's text is added to
# it, with the CHUNK_OVERLAP characters the chunker repeated dropped so the join
# does not say the same sentence twice. It never crosses a document boundary,
# and never crosses a heading - a heading-opened chunk carries no overlap prefix,
# which is exactly how expansion recognises one.
#
#   targeted  joins the NEXT chunk when it opens with 다만/단,/그러나/예외적으로/
#             이 경우, and the PREVIOUS chunk when this one opens referring to
#             text it does not contain (이 경우/위 /앞의 ).
#   blanket   joins both neighbours whenever the boundary permits.
#
# Measured on the 21-question set at top_n=14: anchor@14 0.857 off, 0.905
# targeted, 0.952 blanket, for +6% and +73% evidence tokens respectively.
# targeted ties blanket at top_n 4 and 6 and trails it by one question of 21
# above that, at a tenth of the cost - see the note in app/core/config.py for
# the full table and for what this does NOT fix. Expansion stops when
# ANSWER_CONTEXT_TOKEN_BUDGET would bind, so turning it on can never push an
# evidence item out of the answer; it can only lose its own additions.
NEIGHBOR_EXPANSION=targeted
```

---

#### The trace

Produces `TraceEvidenceItem.neighbors`, `TraceRetrieval.neighbor_expansion` and `TraceNeighbor`, consumed by `GET /api/messages/{id}/trace` and the 추적 dialog.

- [ ] **Step 8: Modify `backend/app/chat/service.py`**

```python
                # What neighbour expansion folded into this item's content. The
                # identity fields above still name the primary chunk - an expanded
                # item is ONE citation - so this list is the only thing on the
                # screen that says the text is wider than the chunk it cites.
                "neighbors": metadata.get("neighbors") or [],
```

- [ ] **Step 9: Modify `backend/app/schemas/observability.py`**

```python
    # What neighbour expansion merged into this item, one entry per neighbour:
    # chunk_id, chunk_index, offset (-1 before / +1 after), page, reason and
    # tokens. Empty for every item when NEIGHBOR_EXPANSION is off, and for every
    # trace written before it existed. `dict`, not a model: it is a record for a
    # human reading the screen, not a contract anything computes on.
    neighbors: list[dict] = Field(default_factory=list)
```

And the knob itself, recorded per answer the way `sparse_weight` and `token_budget` already are:

```python
    # None on every trace written before neighbour expansion existed, which is
    # honestly different from "off" - it means nobody recorded a value, not that
    # the value was off.
    neighbor_expansion: str | None = None
```

- [ ] **Step 10: Modify `frontend/lib/types.ts`**

```typescript
/** One chunk that neighbour expansion folded into an evidence item's content.
 *
 * `offset` is -1 for the chunk before the cited one and +1 for the one after.
 * The item's own chunk_id/page/section still name the PRIMARY chunk - an
 * expanded item is one citation, not several - so this is the only thing on the
 * trace screen that says the text shown to the model was wider than that chunk.
 * `reason` is why it was merged: "proviso" (the next chunk opened with 다만 /
 * 그러나 / …), "dangling" (this chunk opened referring to text it did not
 * contain) or "blanket". */
export interface TraceNeighbor {
  chunk_id: string;
  chunk_index: number;
  offset: number;
  page: number | null;
  reason: string;
  tokens: number;
}
```

- [ ] **Step 11: Modify `frontend/components/chat/TraceDialog.tsx`**

```tsx
const NEIGHBOR_REASON: Record<string, string> = {
  proviso: "단서 이어붙임",
  dangling: "앞 문맥 보충",
  blanket: "일괄 확장",
};
```

```tsx
        {item.neighbors.length > 0 ? (
          // The row still cites ONE chunk; this line is what stops that being a
          // lie about how much text the model actually read.
          <p className="mt-1 text-caption text-primary">
            {`앞뒤 청크 ${item.neighbors.length}개 합침 · `}
            {item.neighbors
              .map((n) => `${n.offset < 0 ? "앞" : "뒤"} #${n.chunk_index} (${NEIGHBOR_REASON[n.reason] ?? n.reason})`)
              .join(", ")}
          </p>
        ) : null}
```

---

#### The measurement

Produces `--expansion`, per-`group` reporting and `expand_selection`. The table in `app/core/config.py` is regenerated with `python scripts/eval_retrieval.py --variants current --expansion off,targeted,blanket`.

- [ ] **Step 12: Modify `scripts/eval_retrieval.py`**

The harness calls the SHIPPED `expand`, not a re-implementation, so what it measures is the product:

```python
async def expand_selection(session, selected, meta, *, mode, settings, query):
    """The selected ids as RetrievedChunks, with neighbour expansion applied.

    Calls the SHIPPED `app.retrieval.neighbors.expand` - the same function
    hybrid_search calls, with the same CHUNK_OVERLAP and the same token budget -
    so what this measures is the product and not a second implementation of it.
    mode="off" returns the chunks untouched, which is exactly what the shipped
    code does, so the "off" row of the table is not a separate code path either.
    """
    from app.retrieval.evidence import RetrievedChunk
    from app.retrieval.neighbors import expand

    chunks = [
        RetrievedChunk(
            chunk_id=cid,
            document_id=str(meta[cid].document_id),
            filename="",
            content=meta[cid].content,
            page=meta[cid].page,
            section=meta[cid].section,
            chunk_index=meta[cid].chunk_index,
        )
        for cid in selected
        if cid in meta
    ]
    await expand(
        session,
        chunks,
        mode=mode,
        overlap_chars=settings.chunk_overlap,
        token_budget=settings.answer_context_token_budget,
        query=query,
    )
    return chunks
```

```python
    parser.add_argument(
        "--expansion",
        default="off",
        help="comma-separated NEIGHBOR_EXPANSION modes to compare: off, targeted, blanket.",
    )
```

Retrieval runs once per variant and every expansion mode is measured over the same selections — expansion adds text to slots already won, so re-running the search per mode would only re-derive an identical ranking. Metrics are reported per `group`:

```python
        modes = [m.strip() for m in args.expansion.split(",") if m.strip()]
        # Insertion order, so "base" (the 21 original questions) stays first.
        groups = list(dict.fromkeys(entry.get("group", "base") for entry in questions))

        for cfg_k, cfg_limit, cfg_w in configs:
            header = (
                f"top_n={top_n}  candidate_limit={cfg_limit}  rrf_k={cfg_k}  sparse_weight={cfg_w}"
            )
            print(f"\n{header}\n{'-' * len(header)}")
            print(
                f"{'variant/expansion':<24} {'group':<9} {'n':>3} {'recall@' + str(top_n):>9} "
                f"{'anchor@' + str(top_n):>9} {'prec@' + str(top_n):>9} {'overlap':>8} "
                f"{'sparse-noise':>13} {'tokens':>7} {'expanded':>9}"
            )
            for name in wanted:
                fn = variants[name]
                # Retrieval runs ONCE per variant and every expansion mode is
                # measured over the same selections. Expansion adds text to slots
                # that were already won, so re-running the search per mode would
                # only re-derive an identical ranking.
                runs = []
                for entry in questions:
                    dense_ids = dense[entry["id"]][:cfg_limit]
                    sparse_ids = await fn(session, entry["question"], cfg_limit)
                    if cfg_w == 1.0:
                        fused = reciprocal_rank_fusion([dense_ids, sparse_ids], k=cfg_k)
                    else:
                        # Weighted RRF, kept here rather than in the shipped pure
                        # function until the numbers say it earns a signature change.
                        acc: dict[str, float] = defaultdict(float)
                        for rank, cid in enumerate(dict.fromkeys(dense_ids), 1):
                            acc[cid] += 1 / (cfg_k + rank)
                        for rank, cid in enumerate(dict.fromkeys(sparse_ids), 1):
                            acc[cid] += cfg_w / (cfg_k + rank)
                        fused = sorted(acc.items(), key=lambda p: -p[1])
                    selected = [chunk_id for chunk_id, _ in fused[:top_n]]
                    runs.append((entry, selected, dense_ids, sparse_ids))
                    if args.show == entry["id"]:
                        gold = set(entry["gold_pages"])
                        print(f"  [{name}] {entry['id']}")
                        for i, cid in enumerate(selected, 1):
                            mark = "HIT " if pages.get(cid) in gold else "    "
                            print(
                                f"    {mark}{i}. page={pages.get(cid)} "
                                f"dense={dense_ids.index(cid) + 1 if cid in dense_ids else '-'} "
                                f"sparse={sparse_ids.index(cid) + 1 if cid in sparse_ids else '-'}"
                            )

                for mode in modes:
                    per_group = defaultdict(lambda: defaultdict(list))
                    for entry, selected, dense_ids, sparse_ids in runs:
                        gold = set(entry["gold_pages"])
                        chunks = await expand_selection(
                            session, selected, meta, mode=mode, settings=settings,
                            query=entry["question"],
                        )
                        hit, hits = score([pages.get(cid) for cid in selected], gold)
                        bucket = per_group[entry.get("group", "base")]
                        bucket["recall"].append(hit)
                        bucket["anchor"].append(
                            anchor_hit([c.content for c in chunks], entry["anchor"])
                        )
                        bucket["prec"].append(hits / top_n)
                        bucket["overlap"].append(len(set(dense_ids) & set(sparse_ids)))
                        # slots that only the sparse side put there AND that miss gold
                        bucket["noise"].append(
                            sum(
                                1
                                for cid in selected
                                if cid not in dense_ids[:top_n] and pages.get(cid) not in gold
                            )
                        )
                        bucket["tokens"].append(sum(count_tokens(c.content) for c in chunks))
                        bucket["expanded"].append(sum(1 for c in chunks if c.neighbors))
                        if args.detail:
                            print(
                                f"    {mode:<9} {entry['id']:<28} "
                                f"{round(bucket['prec'][-1] * top_n)}/{top_n} "
                                f"anchor={bucket['anchor'][-1]} exp={bucket['expanded'][-1]}"
                            )
                    for group in groups:
                        bucket = per_group.get(group)
                        if not bucket:
                            continue
                        n = len(bucket["recall"])
                        mean = lambda key: sum(bucket[key]) / n  # noqa: E731
                        print(
                            f"{name + '/' + mode:<24} {group:<9} {n:>3} {mean('recall'):>9.3f} "
                            f"{mean('anchor'):>9.3f} {mean('prec'):>9.3f} {mean('overlap'):>8.2f} "
                            f"{mean('noise'):>13.2f} {mean('tokens'):>7.0f} {mean('expanded'):>9.2f}"
                        )
```

- [ ] **Step 13: Modify `scripts/eval_questions_ko.json`**

Eight questions derived from the measured proviso pairs. Each was written by reading the pair first: the rule is in chunk N, the 다만/그러나 that qualifies it is in chunk N+1, and `anchor` is the proviso sentence. THE PHRASING IS NEUTRAL ON PURPOSE — a first draft asked "예외 없이 …?" / "절대 …?", which names the exception in the query and pulls the proviso chunk in on its own; a question that cannot fail is a question that cannot measure.

```json
{
      "id": "q22-무능력자-법정대리인-예외",
      "group": "neighbor",
      "question": "미성년자가 특허출원 등 특허에 관한 절차를 밟으려면 어떻게 해야 하나요?",
      "gold_pages": [
        49
      ],
      "anchor": "미성년자와 피한정후견인이 독립하여 법률행위를 할 수 있는 경우는 그러하지 아니하다"
    },
    {
      "id": "q23-법인격없는사단-당사자능력",
      "group": "neighbor",
      "question": "동창회나 종친회 같은 법인격 없는 단체는 특허에 관한 절차에서 어떤 지위를 갖나요?",
      "gold_pages": [
        52
      ],
      "anchor": "대표자 또는 관리인이 정해져 있는 경우에는 그 사단 또는 재단의 이름으로 출원심사청구"
    },
    {
      "id": "q24-법정대리인-후견인-특별수권",
      "group": "neighbor",
      "question": "법정대리인이 본인을 대리하여 특허에 관한 절차를 밟을 때 특별수권이 필요한가요?",
      "gold_pages": [
        59
      ],
      "anchor": "법정대리인이라 하더라도 친권자와 후견인은 구분하고 있는데"
    },
    {
      "id": "q25-복대리인-법정대리인-책임",
      "group": "neighbor",
      "question": "복대리인을 선임한 법정대리인은 복대리인의 행위에 대해 어떤 책임을 지나요?",
      "gold_pages": [
        67
      ],
      "anchor": "다만, 부득이한 사유가 있는 때는 그 선임감독에 대하여만 책임이 있다"
    },
    {
      "id": "q26-기간계산-초일불산입-예외",
      "group": "neighbor",
      "question": "특허법상 기간을 계산할 때 초일은 어떻게 처리하나요?",
      "gold_pages": [
        72,
        73
      ],
      "anchor": "그 기간이 오전 영시부터 시작하는 때에는 초일도 산입"
    },
    {
      "id": "q27-전산장애-기간만료-예외",
      "group": "neighbor",
      "question": "전산장애로 전자문서가 기한 내에 제출되지 않은 경우 기간은 언제 만료되나요?",
      "gold_pages": [
        74
      ],
      "anchor": "지식재산처장이 사전에 공지한 경우에는 장애로 보지 않는다"
    },
    {
      "id": "q28-지정기간연장-자동승인-예외",
      "group": "neighbor",
      "question": "지정기간 연장신청은 언제 승인된 것으로 보나요?",
      "gold_pages": [
        76
      ],
      "anchor": "이해관계인의 이익이 부당하게 침해되는 것으로 판단하는 경우에는 필요한 기간만 연장승인"
    },
    {
      "id": "q29-보정기간연장-4월상한-예외",
      "group": "neighbor",
      "question": "특허법 제46조에 따른 보정기간의 지정기간연장은 얼마나 연장할 수 있나요?",
      "gold_pages": [
        78
      ],
      "anchor": "신청인이 책임질 수 없는 사유가 발생하거나 국내단계에 진입하는 국제특허출원 등 지정기간의 추가 연장이 필요하다고 인정되는 경우에는 추가 연장이 가능하다"
    }
```

---

### Task 61: Tests

**Files:**
- Create: `backend/tests/test_neighbors.py`
- Modify: `backend/tests/test_observability.py`

**Interfaces:**
- Consumed by: nothing. Every guard here was staged as failing before it was kept — the overlap de-duplication, the document boundary, the heading boundary, the token budget, `off`, the merge metadata, and the citation numbering.

- [ ] **Step 1: Write `backend/tests/test_neighbors.py`**

The fixture is chunked by the REAL chunker rather than by hand: expansion's heading test is "did the chunker carry an overlap prefix across this boundary", and a hand-written fixture would let the test agree with a de-duplicator that does not match what is stored.

The second document's chunk is built to look EXACTLY like a legitimate size-split continuation of the first document's last chunk — same section, and an opening line that repeats the previous chunk's tail. Without that, the overlap test blocks it on its own and "does not cross a document boundary" passes with the document dropped from the key. That is not a hypothetical: the break-it-and-watch-it-fail pass caught precisely this.

```python
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
from app.retrieval.reranker import NoneReranker
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
        NoneReranker(),
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
```

- [ ] **Step 2: Modify `backend/tests/test_observability.py`**

`NEIGHBOR_EXPANSION` joins the env-only list, which that test pins exactly so a new key cannot appear on the settings screen unnoticed.

```python
    assert {item["key"] for item in body["env_only"]} == {
        "EMBEDDING_MODEL",
        "EMBEDDING_DIM",
        # off/targeted/blanket - a choice, not a number, so it has no editable
        # spec and appears here instead. See app/core/settings_store.py.
        "NEIGHBOR_EXPANSION",
    }
```
