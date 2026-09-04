"""What each ARM returned for a question, and what the fused set looks like.

    docker compose exec -T backend python /app/scripts/probe_arms.py "질문" [needle]

probe_36_rank.py answers "where is ONE known chunk in the dense arm". This
answers the question that one cannot: WHO IS IN THE CANDIDATE SET AND WHY. It
runs the deployed dense and sparse arms at the deployed candidate_limit, fuses
them with the deployed weights, and prints the fused slots with the per-arm rank
behind each one - so "the re-cut document displaced the classification table"
stops being a hypothesis and becomes two rank columns.

It also prints exactly what evidence_is_weak() reads: bestRRF against the
threshold, and whether ANY fused candidate was found by both arms. Those are the
only two inputs to the clarify branch, and reading them here costs an embedding
rather than an answer completion.

`needle` is an optional SQL LIKE fragment; every chunk matching it gets its rank
in each arm printed even when it is nowhere near the candidate set.
"""

import asyncio
import sys

sys.path.insert(0, "/app")

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import make_engine, make_sessionmaker  # noqa: E402
from app.core.settings_store import effective_settings  # noqa: E402
from app.llm.openai_provider import OpenAIProvider  # noqa: E402
from app.retrieval.keyword_search import keyword_search  # noqa: E402
from app.retrieval.rrf import reciprocal_rank_fusion  # noqa: E402
from app.retrieval.vector_store import PgVectorStore  # noqa: E402


def head(content: str, width: int = 74) -> str:
    return " ".join(content.split())[:width]


async def main() -> None:
    question = sys.argv[1]
    needle = sys.argv[2] if len(sys.argv) > 2 else None
    base = get_settings()
    engine = make_engine(base)
    session = make_sessionmaker(engine)
    # THE DEPLOYED CONFIGURATION LIVES IN THE DATABASE, not in the environment:
    # TOP_N and CANDIDATE_LIMIT are settings-store overrides, so a probe that
    # reads get_settings() alone measures a configuration nobody runs - the exact
    # mistake commit 2d3bc92 was about.
    async with session() as bootstrap:
        settings = await effective_settings(bootstrap, base)
    provider = OpenAIProvider(
        api_key=settings.openai_api_key,
        embedding_model=settings.embedding_model,
        answer_model=settings.answer_model,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        batch_size=settings.embedding_batch_size,
        batch_chars=settings.embedding_batch_chars,
        embedding_dim=settings.embedding_dim,
    )
    limit = settings.retrieval_candidate_limit
    print(f"Q: {question}")
    print(
        f"cfg: top_n={settings.retrieval_top_n} candidate_limit={limit} "
        f"rrf_k={settings.rrf_k} sparse_weight={settings.sparse_weight} "
        f"tokenizer={settings.sparse_tokenizer} weak_rrf={settings.weak_evidence_rrf_score}"
    )
    embedding = (await provider.embed([question]))[0]

    async with session() as db:
        store = PgVectorStore(db)
        dense = [hit.chunk_id for hit in await store.search(embedding, limit, None)]
        sparse = await keyword_search(
            db, question, limit, None, tokenizer=settings.sparse_tokenizer
        )
        fused = reciprocal_rank_fusion(
            [dense, sparse], k=settings.rrf_k, weights=[1.0, settings.sparse_weight]
        )[:limit]

        dense_rank = {cid: i for i, cid in enumerate(dense, start=1)}
        sparse_rank = {cid: i for i, cid in enumerate(sparse, start=1)}
        ids = [cid for cid, _ in fused]
        rows = {
            str(r.id): r
            for r in (
                await db.execute(
                    text(
                        "select c.id, c.page, c.chunk_index, c.content, d.filename "
                        "from chunks c join documents d on d.id = c.document_id "
                        "where c.id = any(cast(:ids as uuid[]))"
                    ),
                    {"ids": ids},
                )
            ).all()
        }

        print(f"\nFUSED (top {len(fused)}; * = both arms):")
        for slot, (cid, score) in enumerate(fused, start=1):
            row = rows.get(cid)
            both = "*" if cid in dense_rank and cid in sparse_rank else " "
            print(
                f" {slot:>2}{both} rrf={score:.4f} d={dense_rank.get(cid, '-'):>3} "
                f"s={sparse_rank.get(cid, '-'):>3} "
                f"{(row.filename if row else '?')[:14]:<14} "
                f"p.{(row.page if row and row.page is not None else '?'):<4} "
                f"{head(row.content) if row else ''}"
            )

        best = fused[0][1] if fused else 0.0
        corroborated = any(cid in dense_rank and cid in sparse_rank for cid, _ in fused)
        print(
            f"\nweak-evidence inputs: bestRRF={best:.4f} "
            f"(threshold {settings.weak_evidence_rrf_score}; "
            f"score arm fires={best < settings.weak_evidence_rrf_score}) "
            f"candidates_corroborated={corroborated} "
            f"(corroboration arm fires={not corroborated})"
        )

        if needle:
            targets = (
                await db.execute(
                    text(
                        "select c.id::text as id, c.page, c.chunk_index, c.content, d.filename "
                        "from chunks c join documents d on d.id = c.document_id "
                        "where c.content like :needle order by c.chunk_index"
                    ),
                    {"needle": needle},
                )
            ).all()
            # `needle` is handed to LIKE verbatim, so a fragment without its own
            # %wildcards% matches nothing - and a needle that matched nothing used
            # to crash on scored[0] three lines into the diagnosis it was run to
            # produce. The count is printed either way and the walk below is
            # skipped when there is nothing to walk from.
            print(f"\nNEEDLE {needle!r}: {len(targets)} chunks (LIKE - wrap it in %)")
            vector = "[" + ",".join(f"{v:.7g}" for v in embedding) + "]"
            scored = []
            for row in targets:
                drank = await db.scalar(
                    text(
                        "select 1 + count(*) from chunks where embedding is not null "
                        "and embedding <=> cast(:v as vector) < "
                        "(select embedding <=> cast(:v as vector) from chunks where id = :t)"
                    ),
                    {"v": vector, "t": row.id},
                )
                scored.append((drank, sparse_rank.get(row.id, None), row))
            scored.sort(key=lambda t: t[0])
            for drank, srank, row in scored[:8]:
                print(
                    f"  dense#{drank:<7} sparse#{srank if srank else '-':<5} "
                    f"p.{row.page:<4} {head(row.content)}"
                )

            # WHO IS ACTUALLY IN FRONT OF IT. "The re-cut document displaced the
            # table" is only a hypothesis until the chunks ranked ABOVE the best
            # needle chunk are counted BY DOCUMENT: if the re-cut document is not
            # most of them, it did not displace anything and the cause is
            # elsewhere. This is the one query that can tell those two apart.
            best_target = scored[0][2] if scored else None
            ahead = () if best_target is None else (
                await db.execute(
                    text(
                        "select d.filename, count(*) as n from chunks c "
                        "join documents d on d.id = c.document_id "
                        "where c.embedding is not null "
                        "and c.embedding <=> cast(:v as vector) < "
                        "(select embedding <=> cast(:v as vector) from chunks where id = :t) "
                        "group by d.filename order by n desc"
                    ),
                    {"v": vector, "t": best_target.id},
                )
            ).all()
            if best_target is not None:
                print(
                    f"\nDENSE-AHEAD of the best needle chunk "
                    f"(rank {scored[0][0]}), by document:"
                )
            for row in ahead:
                print(f"  {row.n:>6}  {row.filename}")

            # THE SPARSE ARM AT DEPTH. The arm is cut at candidate_limit=10, so
            # "not in the sparse top 10" says nothing about whether the chunk is
            # at sparse rank 11 or 3000 - and those are different diagnoses. The
            # per-document histogram of the deep list is what shows one document
            # occupying the head of the arm.
            depth = int(sys.argv[3]) if len(sys.argv) > 3 else 300
            deep = await keyword_search(
                db, question, depth, None, tokenizer=settings.sparse_tokenizer
            )
            deep_rank = {cid: i for i, cid in enumerate(deep, start=1)}
            hits = sorted(deep_rank[r.id] for r in targets if r.id in deep_rank)
            print(
                f"\nSPARSE at depth {len(deep)}: needle chunks at {hits[:10] or 'none in list'}"
            )
            # WHAT SHAPE the chunks at the head of the sparse arm are. ts_rank has
            # no IDF and no length normalisation by default, so a short chunk made
            # entirely of a heading scores on density alone. `body` is what is left
            # after the ancestor line the hierarchical chunker prepends; body=0
            # means the chunk IS its heading and carries no text of its own.
            shapes = (
                await db.execute(
                    text(
                        "select c.id::text as id, d.filename, c.page, "
                        "length(c.content) as total, "
                        "length(split_part(c.content, chr(10), 1)) as prefix, "
                        "split_part(c.content, chr(10), 1) as head "
                        "from chunks c join documents d on d.id = c.document_id "
                        "where c.id = any(cast(:ids as uuid[]))"
                    ),
                    {"ids": deep[:15]},
                )
            ).all()
            by_id = {r.id: r for r in shapes}
            print("\nSPARSE HEAD (rank: total/prefix chars, body = total-prefix):")
            for rank, cid in enumerate(deep[:15], start=1):
                r = by_id.get(cid)
                if r is None:
                    continue
                print(
                    f" s#{rank:<3} {r.total:>4}/{r.prefix:<4} body={r.total - r.prefix:<4} "
                    f"{r.filename[:12]:<12} p.{r.page:<4} {head(r.head, 62)}"
                )

            deep_docs = (
                await db.execute(
                    text(
                        "select c.id::text as id, d.filename from chunks c "
                        "join documents d on d.id = c.document_id "
                        "where c.id = any(cast(:ids as uuid[]))"
                    ),
                    {"ids": deep},
                )
            ).all()
            name_of = {r.id: r.filename for r in deep_docs}
            for cut in (10, 20, 50, 100, 300, len(deep)):
                if cut > len(deep):
                    continue
                tally: dict[str, int] = {}
                for cid in deep[:cut]:
                    tally[name_of.get(cid, "?")] = tally.get(name_of.get(cid, "?"), 0) + 1
                ordered = sorted(tally.items(), key=lambda kv: -kv[1])
                print(f"  top {cut:>4}: " + "  ".join(f"{n}×{k[:12]}" for k, n in ordered))

    await provider.aclose()
    await engine.dispose()


asyncio.run(main())
