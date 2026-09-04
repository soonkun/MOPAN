"""Citation edges, built at ingest, resolved with no model.

DETERMINISTIC, FREE AND VERIFIABLE, which is why there is no LLM here and must
not be one. `제1조제1항`, `제12조제8항`, `[특법54(3)]` are machine-parseable strings
that state their own depth; asking a model to read them would cost money per
document, vary between runs, and hide a wrong answer inside a plausible one. A
semantic layer (요건/예외/판단절차) can sit ON TOP of this later. It cannot replace
it.
"""

import logging
import re
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.chunk_edge import ChunkEdge
from app.models.document import Document
from app.rag.chunking.base import ChunkCandidate
from app.rag.chunking.hierarchy import (
    Citation,
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


def _path_index(keys: list[str | None], scheme: Scheme) -> dict[str, int]:
    """문서 순서의 path 키 목록 -> 각 경로를 여는 첫 위치.

    path -> the FIRST piece that carries it. A run longer than the size bound
    ships as several chunks all holding the same path; a citation to that clause
    wants the piece that opens it, the one carrying the clause's own first
    sentence.

    A path TWO runs opened is AMBIGUOUS and resolves to nothing. 상표심사기준
    prints 【상표법】 제28조 and 【상표법시행규칙】 제28조, and the citing chunk
    almost never spells out which law it means. MEASURED, in a live answer
    before this guard: the 시행규칙 chunk's own "제28조" attached 상표법 제28조
    (서류 제출의 효력 발생 시기) as the definition of what goes on the form. A
    WRONG provision is worse than a missing one. The longest-prefix walk in
    `resolve_citation` still rescues "제28조제2항" when only one 제28조 has a
    제2항.

    A PATH NO CHUNK OPENS resolves to the first chunk that opens under it.
    특허법 writes 제36조 as a bare heading with the text entirely in its 항, so
    "제36조" as a citation lands on the piece opening 제36조제1항. MEASURED
    before this existed: 3,026 of 5,923 resolved edges landed on heading-only
    chunks - half of every citation delivered was an article TITLE.
    """
    index: dict[str, int] = {}
    ambiguous: set[str] = set()
    for position, key in enumerate(keys):
        if not key:
            continue
        if key in index:
            ambiguous.add(key)
            continue
        index[key] = position

    opened = set(index)
    previous: tuple[tuple[str, str], ...] = ()
    for position, key in enumerate(keys):
        path = parse_key(key or "", scheme)
        if not path:
            continue
        for cut in range(1, len(path)):
            prefix = path_key(path[:cut])
            if prefix in opened or previous[:cut] == path[:cut]:
                continue
            if prefix in index:
                ambiguous.add(prefix)
            else:
                index[prefix] = position
        previous = path

    for key in ambiguous:
        index.pop(key, None)
    return index


def _document_title(filename: str) -> str:
    """파일명 -> 정규화된 제목. "특허법 시행규칙.html" -> "특허법시행규칙"."""
    return re.sub(r"\s+", "", re.sub(r"\.[A-Za-z0-9]+$", "", filename))


def _resolve_in(index: dict[str, int], path: tuple[tuple[str, str], ...]) -> int | None:
    """가장 긴 접두가 실제로 열려 있는 위치. resolve_citation 규칙 3과 같다."""
    for cut in range(len(path), 0, -1):
        position = index.get(path_key(path[:cut]))
        if position is not None:
            return position
    return None


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

    index = _path_index([candidate.metadata.get("path") for candidate in candidates], scheme)

    edges: list[dict] = []
    found = resolved = 0
    unresolved_examples: list[str] = []
    # 법명이 명시된 인용(「특허법」 제3조 …)은 이 문서의 인덱스로는 해소할 수
    # 없다. 모아 뒀다가 아래에서 코퍼스의 그 법령 문서에 대고 해소한다.
    external: list[tuple[uuid.UUID, Citation]] = []

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
            if citation.law:
                external.append((src, citation))
                continue
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

    # ---- 문서 간 해소 -------------------------------------------------------
    # 문서↔법령 동일성은 추측이 아니라 파일명이다: 정규화한 파일명("특허법
    # 시행규칙.html" -> "특허법시행규칙")이 인용된 법명과 같을 때만 잇는다.
    # 같은 제목의 문서가 둘이면 모호이므로 잇지 않는다 - 잘못된 조문은 없는
    # 조문보다 나쁘다는, 같은 문서 안 모호 규칙과 같은 비대칭.
    #
    # 이것이 chunk_edges에 문서 간 간선이 생기는 유일한 경로다. 이 간선이
    # 생기기 전에는 "「특허법」 제80조 … 를 준용한다"는 실용신안법 선언이
    # 그래프 어디에도 없었고, 역방향 걷기(retrieval/references.py의 _CITERS)가
    # 걸을 것도 없었다.
    if external:
        docs = (await db.execute(select(Document.id, Document.filename))).all()
        titles: dict[str, uuid.UUID | None] = {}
        own_title = ""
        for doc_id, filename in docs:
            title = _document_title(filename)
            if doc_id == document_id:
                own_title = title
            titles[title] = None if title in titles else doc_id

        # 대상 문서의 path 인덱스는 법령당 한 번만 만든다.
        target_cache: dict[uuid.UUID, tuple[dict[str, int], dict[int, uuid.UUID]]] = {}

        async def target_index(doc_id: uuid.UUID) -> tuple[dict[str, int], dict[int, uuid.UUID]]:
            cached = target_cache.get(doc_id)
            if cached is None:
                target_rows = (
                    await db.execute(
                        select(Chunk.id, Chunk.chunk_metadata["path"].astext)
                        .where(Chunk.document_id == doc_id)
                        .order_by(Chunk.chunk_index)
                    )
                ).all()
                cached = (
                    _path_index([key for _, key in target_rows], scheme),
                    {i: chunk_id for i, (chunk_id, _) in enumerate(target_rows)},
                )
                target_cache[doc_id] = cached
            return cached

        for src, citation in external:
            law = re.sub(r"\s+", "", citation.law)
            destination = None
            if law == own_title:
                # 자기 법을 낫표로 부른 경우 - 자기 인덱스로 해소한다.
                position = _resolve_in(index, citation.path)
                destination = by_index.get(position) if position is not None else None
            else:
                target_doc = titles.get(law)
                if target_doc is not None:
                    target_paths, target_ids = await target_index(target_doc)
                    position = _resolve_in(target_paths, citation.path)
                    destination = target_ids.get(position) if position is not None else None
            if destination == src:
                found -= 1
                continue
            if destination is not None:
                resolved += 1
            elif (
                len(unresolved_examples) < UNRESOLVED_EXAMPLES
                and citation.label not in unresolved_examples
            ):
                unresolved_examples.append(citation.label)
            edges.append(
                {
                    "id": uuid.uuid4(),
                    "document_id": document_id,
                    "src_chunk_id": src,
                    "dst_chunk_id": destination,
                    "kind": "ref",
                    "label": citation.label[:200],
                    # 법명을 경로에 함께 적는다 ("특허법#조64"). 대상 문서가
                    # 재적재돼 dst가 비워졌을 때(0015 SET NULL) relink_external이
                    # 이 프리픽스로 자기를 가리키는 간선을 찾아 도로 잇는다.
                    "target_path": f"{law}#{path_key(citation.path)}",
                }
            )

    # 삽입 직전 dst 생존 검증. 문서 간 해소는 남의 문서 청크를 가리키는데, 그
    # 문서가 동시에 재적재되면 방금 찾은 id가 삽입 전에 사라질 수 있다 - 실제로
    # 동시 재적재 8건 중 3건이 FK 위반으로 통째로 실패했다. 죽은 dst는
    # 미해소로 강등하고, 대상이 다시 색인될 때 relink가 잇는다.
    destinations = {e["dst_chunk_id"] for e in edges if e["dst_chunk_id"] is not None}
    if destinations:
        alive = {
            row[0]
            for row in (await db.execute(select(Chunk.id).where(Chunk.id.in_(destinations)))).all()
        }
        for e in edges:
            if e["dst_chunk_id"] is not None and e["dst_chunk_id"] not in alive:
                e["dst_chunk_id"] = None
                if e["kind"] == "ref":
                    resolved -= 1

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


async def relink_external(db: AsyncSession, document_id: uuid.UUID, scheme: Scheme) -> int:
    """방금 색인된 문서를 법명으로 가리키는, 다른 문서의 미해소 간선을 다시 잇는다.

    두 경우가 여기로 온다: ① 인용하는 문서가 먼저 들어와서 대상 법령이 아직
    없었던 경우, ② 대상 문서가 재적재되면서 0015의 SET NULL이 dst를 비운 경우.
    어느 쪽이든 간선 행에는 라벨과 "법명#경로"가 그대로 남아 있으므로, 대상
    문서의 path 인덱스에 대고 같은 최장-접두 규칙으로 도로 잇기만 하면 된다.

    돌려주는 값은 다시 이은 간선 수 - 로그가 찍는다.
    """
    filename = (
        await db.execute(select(Document.filename).where(Document.id == document_id))
    ).scalar_one_or_none()
    if filename is None:
        return 0
    title = _document_title(filename)
    # 같은 제목의 문서가 또 있으면 모호 - build_edges의 같은 규칙.
    twins = (await db.execute(select(Document.filename))).scalars().all()
    if sum(1 for f in twins if _document_title(f) == title) > 1:
        return 0

    edges = (
        (
            await db.execute(
                select(ChunkEdge).where(
                    ChunkEdge.kind == "ref",
                    ChunkEdge.dst_chunk_id.is_(None),
                    ChunkEdge.target_path.like(f"{title}#%"),
                    ChunkEdge.document_id != document_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if not edges:
        return 0

    rows = (
        await db.execute(
            select(Chunk.id, Chunk.chunk_metadata["path"].astext)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index)
        )
    ).all()
    index = _path_index([key for _, key in rows], scheme)
    ids = {i: chunk_id for i, (chunk_id, _) in enumerate(rows)}

    relinked = 0
    for edge in edges:
        path = parse_key(edge.target_path.split("#", 1)[1], scheme)
        if not path:
            continue
        position = _resolve_in(index, path)
        if position is None:
            continue
        edge.dst_chunk_id = ids[position]
        relinked += 1
    return relinked
