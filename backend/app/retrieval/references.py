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

# ---- 역방향: 이 청크를 인용하는 쪽 ------------------------------------------
#
# 준용이 사는 방향이다. "제80조, 제81조 … 를 준용한다"라고 선언하는 실용신안법
# 제20조는 조문 번호 나열뿐이라 질문과 겹치는 어휘가 없어 검색으로는 영원히
# 도달하지 못하고, 정방향 걷기(인용하는 쪽 -> 인용되는 쪽)로도 닿지 않는다 -
# 특허법 조문이 선택됐을 때 필요한 것은 그 조문을 인용하는 선언문이다.
#
# 정방향보다 훨씬 엄격하게 제한하는 이유는 허브다: 인기 조문은 수십 곳에서
# 인용된다. 한국 법령 QA 연구(SearchFireSafety, arXiv 2604.06173)가 인용 그래프
# 전파에 in-degree 페널티를 두지 않으면 허브가 결과를 다시 독점한다고 측정했다.
#
# - 문서가 다른 인용자만 본다. 같은 문서 안의 문맥은 조상 접두와 이웃 확장이
#   이미 나른다. 준용은 정의상 다른 법을 가리킨다.
# - 피인용이 과다한 청크(허브)는 역방향을 아예 붙이지 않는다. 수십 개 중
#   하나를 고르는 것은 어떤 규칙이든 자의적이다.
# - 인용자 중에서는 나가는 ref 간선이 많은 쪽을 고른다. 준용 선언은 조문
#   번호의 목록이라 out-degree가 크고, 해설 문단은 한둘이다.
MAX_CITERS_PER_CHUNK = 1
CITER_HUB_LIMIT = 8

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

# 역방향 후보 전부: 선택된 청크를 인용하는, 다른 문서의 청크들. 인용자마다
# 나가는 ref 간선 수(out_degree)를 함께 가져와 파이썬 쪽에서 허브 판정과
# 우선순위를 정한다 - 후보 수는 root당 많아야 수십이고, 허브 판정에는 어차피
# 전체 개수가 필요하다.
_CITERS = text(
    """
SELECT e.dst_chunk_id AS root, e.src_chunk_id AS chunk_id, e.label,
       c.content, c.page, c.chunk_index,
       (SELECT count(*) FROM chunk_edges o
         WHERE o.src_chunk_id = e.src_chunk_id AND o.kind = 'ref') AS out_degree
  FROM chunk_edges e
  JOIN chunks c ON c.id = e.src_chunk_id
  JOIN chunks target ON target.id = e.dst_chunk_id
 WHERE e.kind = 'ref'
   AND e.dst_chunk_id = ANY(:roots)
   AND c.document_id <> target.document_id
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
    citer_rows = (await db.execute(_CITERS, {"roots": roots})).all()
    if not rows and not citer_rows:
        return

    by_root: dict[str, list] = {}
    for row in rows:
        by_root.setdefault(str(row.root), []).append(row)

    citers_by_root: dict[str, list] = {}
    for row in citer_rows:
        citers_by_root.setdefault(str(row.root), []).append(row)

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

    # 역방향 - 정방향이 예산을 먼저 쓴 뒤에. 저자가 명시적으로 가리킨 것이
    # 이 청크를 가리키는 남보다 더 하중을 진다는, 정방향과 같은 판단.
    for chunk in selected:
        citers = citers_by_root.get(chunk.chunk_id)
        if not citers:
            continue
        if len(citers) > CITER_HUB_LIMIT:
            # 허브: 수십 곳이 인용하는 조문에서 하나를 고르는 것은 어떤
            # 규칙이든 자의적이고, 자의적인 부착은 잡음이다. 붙이지 않는다.
            continue
        already = {note.get("chunk_id") for note in chunk.neighbors}
        citers.sort(key=lambda row: (-row.out_degree, row.chunk_index))
        for row in citers[:MAX_CITERS_PER_CHUNK]:
            if str(row.chunk_id) in already or any(
                other.chunk_id == str(row.chunk_id) for other in selected
            ):
                continue
            body = f"[이 조문을 인용: {row.label}] {row.content}"
            cost = count_tokens(body) + 1
            if total + cost > token_budget:
                continue
            total += cost
            chunk.content = "\n".join([chunk.content, body])
            chunk.neighbors = [
                *chunk.neighbors,
                {
                    "chunk_id": str(row.chunk_id),
                    "chunk_index": row.chunk_index,
                    "offset": 0,
                    "page": row.page,
                    "reason": f"cited-by:{row.label}",
                    "tokens": count_tokens(body),
                },
            ]
            attached += 1

    if attached:
        logger.debug("attached references to %d of %d chunks", attached, len(selected))
