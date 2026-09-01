"""Citation edges, built at ingest, resolved with no model.

DETERMINISTIC, FREE AND VERIFIABLE, which is why there is no LLM here and must
not be one. `제1조제1항`, `제12조제8항`, `[특법54(3)]` are machine-parseable strings
that state their own depth; asking a model to read them would cost money per
document, vary between runs, and hide a wrong answer inside a plausible one. A
semantic layer (요건/예외/판단절차) can sit ON TOP of this later. It cannot replace
it.
"""

import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.chunk_edge import ChunkEdge
from app.rag.chunking.base import ChunkCandidate
from app.rag.chunking.hierarchy import (
    Scheme,
    find_citations,
    parse_key,
    path_key,
    resolve_citation,
)

logger = logging.getLogger("mopan.references")

# How many unresolved citations are shown to the user verbatim on the document
# screen. The COUNT is the metric; a handful of examples is what turns "189
# unresolved" into "[민법950] - you have not uploaded 민법".
UNRESOLVED_EXAMPLES = 5


async def build_edges(
    db: AsyncSession,
    document_id: uuid.UUID,
    scheme: Scheme,
    candidates: list[ChunkCandidate],
) -> dict:
    """Write this document's parent and citation edges; return what to show.

    Runs AFTER the chunks are upserted, because an edge points at chunk rows and
    those ids do not exist until then. The ids are read back by `chunk_index`,
    which is the position `process_document` enumerated the candidates into - so
    `candidates[i]` and `by_index[i]` are the same chunk by construction.

    The whole document's edges are replaced, not merged: re-ingesting is the only
    way a document is re-cut, and a merge would leave edges pointing at chunks
    that no longer exist under a different numbering.
    """
    rows = (
        await db.execute(select(Chunk.id, Chunk.chunk_index).where(Chunk.document_id == document_id))
    ).all()
    by_index = {index: chunk_id for chunk_id, index in rows}

    # path -> the FIRST piece that carries it. A run longer than the size bound
    # ships as several chunks all holding the same path; a citation to that clause
    # wants the piece that opens it, which is the one carrying the clause's own
    # first sentence.
    index: dict[str, int] = {}
    # A path that TWO runs opened. 상표심사기준 prints 【상표법】 제28조 and
    # 【상표법시행규칙】 제28조, and the citing chunk almost never spells out which
    # law it means - so "조28" names two different provisions and there is no
    # deterministic way to pick one.
    #
    # MEASURED, in a live answer before this guard: the 시행규칙 chunk's own
    # "제28조" attached 상표법 제28조(서류 제출의 효력 발생 시기) - a rule about when a
    # filing takes effect, delivered to the model as the definition of what goes on
    # the form. A WRONG provision is worse than a missing one, so an ambiguous key
    # resolves to nothing and the unresolved count says so on screen.
    #
    # The longest-prefix walk in `resolve_citation` still rescues the specific
    # case: "제28조제2항" resolves fine when only one of the two 제28조 has a 제2항.
    ambiguous: set[str] = set()
    for position, candidate in enumerate(candidates):
        key = candidate.metadata.get("path")
        if not key:
            continue
        if key in index:
            ambiguous.add(key)
            continue
        index[key] = position

    # A PATH NO CHUNK OPENS resolves to the first chunk that opens under it.
    # 특허법 writes 제36조 as a bare heading and puts the article's text entirely in
    # its 항, so `hierarchy.walk` no longer emits a chunk for 제36조 itself, and
    # "제36조" as a citation has to land on the piece that opens 제36조제1항 - which
    # is where 그 조 begins and the only place its words are.
    #
    # MEASURED before this existed: of the corpus's 5,923 RESOLVED `ref` edges,
    # 3,026 landed on a heading-only chunk, so half of every citation delivered to
    # the model was an article TITLE and nothing else - and it spent one of the two
    # MAX_REFERENCES_PER_CHUNK slots to say it.
    #
    # The same ambiguity rule, for the same reason: a prefix TWO different runs
    # produce names two provisions - 상표심사기준 prints both 【상표법】제28조 and
    # 【상표법시행규칙】제28조 - and resolves to neither. "Different" means the
    # previous chunk carrying a path did not already sit under it, so the several
    # 항 of one 조 register it once.
    opened = set(index)
    previous: tuple[tuple[str, str], ...] = ()
    for position, candidate in enumerate(candidates):
        path = parse_key(candidate.metadata.get("path") or "", scheme)
        if not path:
            continue
        for cut in range(1, len(path)):
            key = path_key(path[:cut])
            if key in opened or previous[:cut] == path[:cut]:
                continue
            if key in index:
                ambiguous.add(key)
            else:
                index[key] = position
        previous = path

    for key in ambiguous:
        index.pop(key, None)

    edges: list[dict] = []
    found = resolved = 0
    unresolved_examples: list[str] = []

    for position, candidate in enumerate(candidates):
        src = by_index.get(position)
        if src is None:
            continue
        key = candidate.metadata.get("path") or ""
        source_path = parse_key(key, scheme)

        # PARENT. The longest PROPER prefix of this chunk's path that some other
        # chunk opens. Not "the previous chunk": pieces 2..n of one long clause
        # share a path with piece 1, and a 호 that opens no chunk of its own leaves
        # a gap in the ladder that a prefix walk steps over and an index walk does
        # not.
        for cut in range(len(source_path) - 1, 0, -1):
            parent = index.get(path_key(source_path[:cut]))
            if parent is not None and parent != position and by_index.get(parent) is not None:
                edges.append(
                    {
                        "id": uuid.uuid4(),
                        "document_id": document_id,
                        "src_chunk_id": src,
                        "dst_chunk_id": by_index[parent],
                        "kind": "parent",
                        "label": path_key(source_path[:cut])[:200],
                        "target_path": path_key(source_path[:cut]),
                    }
                )
                break

        # REFERENCES. Scanned over the WHOLE chunk text, ancestor line included:
        # that line is what the model will read, so a citation written in it is a
        # citation this chunk delivers. `find_citations` deduplicates, so the
        # ancestor's own citations are counted once for the parent and once for
        # each child that carries them - which is the honest count of "citations
        # reachable from this chunk", not of "citations in the document".
        for citation in find_citations(candidate.content, scheme):
            found += 1
            target = resolve_citation(citation, source_path, index, scheme)
            destination = by_index.get(target) if target is not None else None
            if destination == src:
                # A citation that lands on the chunk that wrote it. The text it
                # points at is already here, so there is nothing to attach, and
                # counting it either way would be a lie: it is neither an
                # unresolved reference to show the user nor a resolved one that
                # earned an edge. The commonest case is the chunk's own ancestor
                # line - "제36조(상표등록출원)" reads as a citation of 제36조.
                found -= 1
                continue
            if destination is not None:
                resolved += 1
            elif len(unresolved_examples) < UNRESOLVED_EXAMPLES and citation.label not in unresolved_examples:
                unresolved_examples.append(citation.label)
            edges.append(
                {
                    "id": uuid.uuid4(),
                    "document_id": document_id,
                    "src_chunk_id": src,
                    "dst_chunk_id": destination,
                    "kind": "ref",
                    "label": citation.label[:200],
                    "target_path": path_key(citation.path),
                }
            )

    await db.execute(delete(ChunkEdge).where(ChunkEdge.document_id == document_id))
    # Chunked for the same asyncpg int16 bind-parameter ceiling PgVectorStore.upsert
    # documents: 특허·실용신안 심사기준 produces thousands of these in one job.
    batch = max(1, 32767 // 7)
    for start in range(0, len(edges), batch):
        await db.execute(ChunkEdge.__table__.insert().values(edges[start : start + batch]))

    return {
        "found": found,
        "resolved": resolved,
        "unresolved": found - resolved,
        "parents": sum(1 for edge in edges if edge["kind"] == "parent"),
        "examples": unresolved_examples,
    }
