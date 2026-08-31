"""Delivery-time reference resolution: a retrieved chunk arrives with the text of
what it cites.

WHY AT DELIVERY AND NOT AT INDEX. Baking the resolution into the chunk would copy
the whole of 제46조제3항 into every chunk that mentions it - 1,102 citations in one
document - and the corpus would carry the same sentences dozens of times, be
embedded on them, and rank on them. A reference is a graph, and a graph is
traversed. The ANCESTOR context is the opposite case and is baked in at index time
(`app/rag/chunking/hierarchy.py`), because a chunk has exactly one ancestor chain
and being findable by it is the whole point.

THE TRAVERSAL IS A RECURSIVE CTE IN POSTGRES, and no graph database was added. The
`crossref` evaluation group is what decides whether that was the right call: if
following citation edges moves it, Postgres is enough; if it does not, that is the
measured case for a typed entity graph and it belongs in a report rather than in a
dependency added on a hunch.

IDENTITY IS UNCHANGED, exactly as neighbour expansion has it: `chunk_id`, `page`
and `section` still name the retrieved chunk, so an enlarged item is still one
citation at one position. Only `content` grows, and `neighbors` records what was
folded in so the trace screen can say so.
"""

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import count_tokens
from app.retrieval.evidence import RetrievedChunk
from app.retrieval.neighbors import FENCE_RESERVE_TOKENS, PER_ITEM_OVERHEAD_TOKENS

logger = logging.getLogger("mopan.references")

# How far a citation chain is followed. 1 = "what this chunk cites"; 2 additionally
# reaches what THAT cites, which is the 준용 chain the `crossref` fixture group
# needs. It is a constant and not a setting because nothing yet measures a
# difference; raise it here, re-run scripts/eval_retrieval.py, and keep the number
# that wins. The traversal is recursive so the depth is the only thing that changes.
REFERENCE_DEPTH = 1

# At most this many cited chunks per retrieved chunk. A clause that cites six
# provisions would otherwise bury the clause itself under six others, and the
# token budget would then be spent by whichever item happened to rank first.
MAX_REFERENCES_PER_CHUNK = 2

# The recursive walk. Depth 1 is every 'ref' edge out of a retrieved chunk; each
# further round follows the refs of what the previous round reached. `visited`
# carries the whole path so a citation cycle - 제1조 cites 제2조 cites 제1조, which
# real statutes do - terminates instead of recursing forever.
_WALK = text(
    """
WITH RECURSIVE walk(root, chunk_id, depth, label, visited) AS (
    SELECT e.src_chunk_id, e.dst_chunk_id, 1, e.label,
           ARRAY[e.src_chunk_id, e.dst_chunk_id]
      FROM chunk_edges e
     WHERE e.kind = 'ref'
       AND e.dst_chunk_id IS NOT NULL
       AND e.src_chunk_id = ANY(:roots)
    UNION ALL
    SELECT w.root, e.dst_chunk_id, w.depth + 1, e.label,
           w.visited || e.dst_chunk_id
      FROM walk w
      JOIN chunk_edges e ON e.src_chunk_id = w.chunk_id
     WHERE e.kind = 'ref'
       AND e.dst_chunk_id IS NOT NULL
       AND w.depth < :depth
       AND NOT e.dst_chunk_id = ANY(w.visited)
)
SELECT DISTINCT ON (w.root, w.chunk_id)
       w.root, w.chunk_id, w.depth, w.label, c.content, c.page, c.chunk_index
  FROM walk w
  JOIN chunks c ON c.id = w.chunk_id
 ORDER BY w.root, w.chunk_id, w.depth
"""
)


async def attach(
    db: AsyncSession,
    selected: list[RetrievedChunk],
    *,
    token_budget: int,
    query: str,
    depth: int = REFERENCE_DEPTH,
) -> None:
    """Append the text of what each selected chunk cites, in place.

    Runs BEFORE neighbour expansion and takes the budget first. That order is a
    judgement and it is stated so it can be argued with: a provision the author
    explicitly pointed at is more load-bearing than the paragraph that happens to
    sit next to the chunk. Neighbour expansion recomputes its running total from
    the contents it is handed, so it sees whatever this spent and cannot
    double-count it.

    THE BUDGET IS A CEILING ON THE WHOLE EVIDENCE SET, the same one neighbour
    expansion honours, so an item that would have reached the model without this
    still reaches it: this can only lose its own additions.
    """
    if not selected or depth < 1 or token_budget <= 0:
        return
    roots = [uuid.UUID(chunk.chunk_id) for chunk in selected]
    rows = (await db.execute(_WALK, {"roots": roots, "depth": depth})).all()
    if not rows:
        return

    by_root: dict[str, list] = {}
    for row in rows:
        by_root.setdefault(str(row.root), []).append(row)

    total = (
        FENCE_RESERVE_TOKENS
        + count_tokens(query)
        + PER_ITEM_OVERHEAD_TOKENS * len(selected)
        + sum(count_tokens(chunk.content) for chunk in selected)
    )
    attached = 0
    for chunk in selected:
        cited = by_root.get(chunk.chunk_id)
        if not cited:
            continue
        # Nearest first: a citation resolved at depth 1 is what this chunk actually
        # said, and one at depth 2 is what its target said. chunk_index breaks the
        # tie so the order is the document's and not the planner's.
        cited.sort(key=lambda row: (row.depth, row.chunk_index))
        parts: list[str] = []
        merged: list[dict] = []
        for row in cited[:MAX_REFERENCES_PER_CHUNK]:
            # A chunk that is already in the evidence set is not repeated: it is
            # in the prompt under its own number, and a second copy would spend the
            # budget to tell the model the same thing twice.
            if any(other.chunk_id == str(row.chunk_id) for other in selected):
                continue
            body = f"[{row.label}] {row.content}"
            cost = count_tokens(body) + 1
            if total + cost > token_budget:
                continue
            total += cost
            parts.append(body)
            merged.append(
                {
                    "chunk_id": str(row.chunk_id),
                    "chunk_index": row.chunk_index,
                    "offset": 0,
                    "page": row.page,
                    "reason": f"ref:{row.label}",
                    "tokens": count_tokens(body),
                }
            )
        if not parts:
            continue
        chunk.content = "\n".join([chunk.content, *parts])
        chunk.neighbors = [*chunk.neighbors, *merged]
        attached += 1

    if attached:
        logger.debug("attached references to %d of %d chunks", attached, len(selected))
