"""Measure Korean retrieval quality against scripts/eval_questions_ko.json.

    python scripts/eval_retrieval.py                      # every variant, default knobs
    python scripts/eval_retrieval.py --variants current,none
    python scripts/eval_retrieval.py --sweep              # rrf_k / candidate_limit grid
    python scripts/eval_retrieval.py --verify             # only check the fixture's anchors

A SCRIPT, not a test: it talks to the running stack's Postgres and embeds each
question once against the live OpenAI API. Embeddings are cached in the system
temp dir keyed by (model, question), so re-running every variant costs zero
further API calls - 20 questions x ~40 tokens is a fraction of a cent once.

Metrics, both against `gold_pages` (PDF page numbers, so they survive a
re-ingestion that renumbers chunk ids):
  recall@N     - fraction of questions with at least one gold-page chunk in the
                 N returned. "did the answer reach the prompt at all".
  precision@N  - mean share of the N slots holding a gold-page chunk. "how much
                 of the evidence budget was spent on the answer".

The reranker is NoneReranker here, so the fused RRF order IS the final order and
what this measures is retrieval, not reranking.

Two variants need throwaway database objects that no migration creates, because
they exist to answer "would this be worth a migration?" and the answer measured
no. Paste this into psql to run `--variants trgm,pgbigram`, and drop it after:

    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE OR REPLACE FUNCTION ko_bigrams(t text) RETURNS tsvector
    LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $fn$
      SELECT to_tsvector('simple'::regconfig, coalesce(string_agg(bg, ' '), ''))
      FROM (
        SELECT substr(w, i, 2) AS bg
        FROM regexp_split_to_table(lower(t), '[^0-9a-z가-힣]+') AS w,
             LATERAL generate_series(1, greatest(length(w) - 1, 1)) AS i
        WHERE w <> ''
      ) s;
    $fn$;
    CREATE TABLE eval_bigrams AS SELECT id, ko_bigrams(content) AS tsv FROM chunks;
    CREATE INDEX eval_bigrams_gin ON eval_bigrams USING gin(tsv);
    -- teardown
    DROP TABLE eval_bigrams; DROP FUNCTION ko_bigrams(text); DROP EXTENSION pg_trgm;
"""

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FIXTURE = Path(__file__).with_name("eval_questions_ko.json")

# Korean is agglutinative: the same noun appears as 공지예외주장은 / 공지예외주장을 /
# 공지예외주장의, which a whitespace tokenizer sees as three unrelated tokens.
# Longest-first so 으로써 wins over 로 and the stem is not shredded twice.
JOSA = (
    "으로써", "에게서", "이라도", "에서는", "에서도", "으로는", "이라는", "하려면",
    "으로", "에서", "에게", "까지", "부터", "이나", "보다", "마다", "라도", "이란",
    "하는", "한다", "된다", "되는", "이며", "와의", "과의", "이라", "라는",
    "은", "는", "이", "가", "을", "를", "에", "도", "만", "와", "과", "로", "의",
)
_TOKEN = re.compile(r"[0-9a-zA-Z가-힣]+")


def stem(token: str) -> str:
    """Strip one trailing josa, but never below 2 characters of stem."""
    for suffix in JOSA:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[: -len(suffix)]
    return token


def ngrams(text: str, n: int) -> list[str]:
    out = []
    for token in _TOKEN.findall(text.lower()):
        if len(token) <= n:
            out.append(token)
        else:
            out.extend(token[i : i + n] for i in range(len(token) - n + 1))
    return out


class Bm25:
    """Okapi BM25 over whatever tokenizer it is handed. In-memory, 1950 docs.

    Here to answer one question before anyone pays for a migration: does the
    Korean failure come from the TOKENIZER (whitespace 어절 vs character n-grams)
    or from the SCORER (ts_rank has no IDF)? Running BM25 over both tokenizers
    separates the two.
    """

    def __init__(self, docs: dict[str, str], tokenize):
        self.tokenize = tokenize
        self.postings: dict[str, list[tuple[str, int]]] = defaultdict(list)
        self.lengths: dict[str, int] = {}
        for chunk_id, text in docs.items():
            counts = Counter(tokenize(text))
            self.lengths[chunk_id] = sum(counts.values()) or 1
            for term, freq in counts.items():
                self.postings[term].append((chunk_id, freq))
        self.n = len(docs)
        self.avgdl = sum(self.lengths.values()) / max(self.n, 1)

    def search(self, query: str, limit: int, k1: float = 1.2, b: float = 0.75) -> list[str]:
        scores: dict[str, float] = defaultdict(float)
        for term in set(self.tokenize(query)):
            posting = self.postings.get(term)
            if not posting:
                continue
            idf = math.log(1 + (self.n - len(posting) + 0.5) / (len(posting) + 0.5))
            for chunk_id, freq in posting:
                norm = 1 - b + b * self.lengths[chunk_id] / self.avgdl
                scores[chunk_id] += idf * freq * (k1 + 1) / (freq + k1 * norm)
        # id as tie-break, same reason keyword_search sorts by id: a ranking that
        # depends on dict order makes RRF non-reproducible.
        ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
        return [chunk_id for chunk_id, _ in ranked[:limit]]


async def variant_current(session, query, limit):
    from app.retrieval.keyword_search import keyword_search

    return await keyword_search(session, query, limit)


async def variant_none(session, query, limit):
    return []


async def variant_prefix(session, query, limit):
    """Josa-strip each query token, then ask tsquery for a PREFIX match.

    '공지예외주장은' -> '공지예외주장':* , which the index answers with every
    lexeme that starts with the stem - 공지예외주장은/을/의 all match. Two
    statements because the stripping happens in Python between them; the token
    list goes back as a bound array, never concatenated into SQL.
    """
    from sqlalchemy import ARRAY, Text, bindparam, func, select, text

    from app.models.chunk import Chunk
    from app.retrieval.keyword_search import KOREAN_STOPWORDS

    lexemes = (
        await session.scalars(
            text(
                """SELECT lexeme FROM unnest(to_tsvector('simple', :q))
                    WHERE to_tsvector('english', lexeme) <> ''::tsvector
                      AND NOT lexeme = ANY(:ko)"""
            ).bindparams(
                bindparam("q", value=query),
                bindparam("ko", value=list(KOREAN_STOPWORDS), type_=ARRAY(Text)),
            )
        )
    ).all()
    stems = sorted({stem(lex) for lex in lexemes})
    if not stems:
        return []
    # A one-character prefix is a wildcard, not a stem: '이':* would match every
    # lexeme starting with 이. Short stems are matched exactly.
    ts = text(
        """to_tsquery('simple',
             (SELECT string_agg(quote_literal(s) || CASE WHEN length(s) > 1 THEN ':*' ELSE '' END,
                                ' | ')
                FROM unnest(:stems) s))"""
    ).bindparams(bindparam("stems", value=stems, type_=ARRAY(Text)))
    query_ = (
        select(Chunk.id)
        .where(Chunk.content_tsv.op("@@", is_comparison=True)(ts))
        .order_by(func.ts_rank(Chunk.content_tsv, ts).desc(), Chunk.id)
        .limit(limit)
    )
    return [str(cid) for cid in await session.scalars(query_)]


async def variant_trgm(session, query, limit):
    """pg_trgm word_similarity: best-matching substring extent, not whole-string.

    similarity() would be hopeless here - a 40-char question against a 900-char
    chunk scores near zero however good the match is.
    """
    from sqlalchemy import bindparam, text

    rows = await session.scalars(
        text(
            """SELECT id FROM chunks
                WHERE word_similarity(:q, content) > 0.3
                ORDER BY word_similarity(:q, content) DESC, id
                LIMIT :lim"""
        ).bindparams(bindparam("q", value=query), bindparam("lim", value=limit))
    )
    return [str(cid) for cid in rows]


async def variant_pgbigram(session, query, limit):
    """Character bigrams as a tsvector, ranked by ts_rank. Needs the throwaway
    `eval_bigrams` table built by the SQL in the report - this is the Postgres
    version of bigram_bm25, and the gap between the two IS the missing IDF."""
    from sqlalchemy import bindparam, text

    rows = await session.scalars(
        text(
            """WITH q AS (
                 SELECT to_tsquery('simple',
                   (SELECT string_agg(quote_literal(lexeme), ' | ')
                      FROM unnest(ko_bigrams(:q)))) AS tq)
               SELECT b.id FROM eval_bigrams b, q
                WHERE b.tsv @@ q.tq
                ORDER BY ts_rank(b.tsv, q.tq) DESC, b.id
                LIMIT :lim"""
        ).bindparams(bindparam("q", value=query), bindparam("lim", value=limit))
    )
    return [str(cid) for cid in rows]


def build_lexical_variants(docs: dict[str, str]) -> dict:
    """BM25 variants, built once over the whole corpus."""
    indexes = {
        "word_bm25": Bm25(docs, lambda t: [w.lower() for w in _TOKEN.findall(t)]),
        "stem_bm25": Bm25(docs, lambda t: [stem(w.lower()) for w in _TOKEN.findall(t)]),
        "bigram_bm25": Bm25(docs, lambda t: ngrams(t, 2)),
        "trigram_bm25": Bm25(docs, lambda t: ngrams(t, 3)),
    }
    # Optional, and NOT a backend dependency: `pip install kiwipiepy` locally to
    # answer "is a real Korean morphological analyser worth adding?" with a number
    # instead of an argument. It installs clean on python:3.13-slim (the backend
    # base image) as a wheel, so the container is not the obstacle - the score is.
    try:
        from kiwipiepy import Kiwi
    except ImportError:
        print("(kiwipiepy not installed - skipping kiwi_bm25; pip install kiwipiepy)")
    else:
        kiwi = Kiwi()
        # Content morphemes only. Josa (J*), endings (E*) and affixes (X*) are
        # exactly the noise the whitespace tokenizer could not strip.
        keep = ("NN", "NP", "NR", "VV", "VA", "SL", "SH", "SN", "XR")

        def kiwi_tokens(text: str) -> list[str]:
            return [t.form for t in kiwi.tokenize(text) if t.tag.startswith(keep)]

        indexes["kiwi_bm25"] = Bm25(docs, kiwi_tokens)

    def make(index):
        async def run(session, query, limit):
            return index.search(query, limit)

        return run

    return {name: make(index) for name, index in indexes.items()}


async def embed_all(provider, model, questions):
    """One embedding call per question, cached on disk so variant sweeps are free."""
    cache_path = Path(tempfile.gettempdir()) / f"mopan-eval-emb-{model}.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    missing = [q for q in questions if hashlib.sha256(q.encode()).hexdigest() not in cache]
    if missing:
        print(f"embedding {len(missing)} question(s) against {model} (rest cached)")
        vectors = await provider.embed(missing)
        for question, vector in zip(missing, vectors, strict=True):
            cache[hashlib.sha256(question.encode()).hexdigest()] = vector
        cache_path.write_text(json.dumps(cache))
    return {q: cache[hashlib.sha256(q.encode()).hexdigest()] for q in questions}


def score(returned_pages: list[int | None], gold: set[int]) -> tuple[int, int]:
    hits = sum(1 for page in returned_pages if page in gold)
    return (1 if hits else 0), hits


def anchor_hit(returned_contents: list[str], anchor: str) -> int:
    """Did a chunk carrying the answer-bearing sentence actually reach the model?

    This exists because recall@N did not catch a real failure. It counts a hit
    on any chunk from a gold PAGE, and a page here holds several chunks: on the
    owner's 공지예외/국내우선권 question it scored a hit for a page-594 chunk that
    restates the rule as a double negative, while the chunk on 593 that states
    it plainly - "...그 공지예외주장을 인정하도록 한다" - sat at fused rank 8 and
    never arrived. The metric reported success and the answer was inverted.
    """
    return 1 if any(anchor in content for content in returned_contents) else 0


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", default="")
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--rrf-k", type=int, default=None)
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument(
        "--weights",
        default="1.0",
        help="comma-separated sparse weights. 1.0 is plain RRF; below 1 the sparse "
        "list still contributes but can no longer outbid the dense list on rank alone.",
    )
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--detail", action="store_true", help="per-question hit counts")
    parser.add_argument("--show", default="", help="question id to print per-slot detail for")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings
    from app.llm.openai_provider import OpenAIProvider
    from app.models.chunk import Chunk
    from app.models.document import Document
    from app.retrieval.rrf import reciprocal_rank_fusion
    from app.retrieval.vector_store import PgVectorStore

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    questions = fixture["questions"]
    settings = get_settings()
    top_n = args.top_n or settings.retrieval_top_n
    limit = args.limit or settings.retrieval_candidate_limit
    rrf_k = args.rrf_k if args.rrf_k is not None else settings.rrf_k

    engine = create_async_engine(settings.database_url.replace("@postgres:", "@127.0.0.1:"))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        rows = (
            await session.execute(
                select(Chunk.id, Chunk.page, Chunk.content)
                .join(Document, Document.id == Chunk.document_id)
                .where(Document.filename == fixture["document_filename"])
            )
        ).all()
        if not rows:
            print(f"no chunks for {fixture['document_filename']!r} - is the corpus ingested?")
            return 1
        pages = {str(cid): page for cid, page, _ in rows}
        docs = {str(cid): content for cid, _, content in rows}
        print(f"corpus: {len(rows)} chunks, {len({p for p in pages.values()})} pages\n")

        # Anchors are the fixture's own regression check: if extraction changes
        # and a gold page no longer carries the passage, every number below is a
        # measurement of the wrong thing.
        bad = []
        for entry in questions:
            gold = set(entry["gold_pages"])
            if not any(entry["anchor"] in c for cid, c in docs.items() if pages[cid] in gold):
                bad.append(entry["id"])
        print(f"anchor check: {len(questions) - len(bad)}/{len(questions)} ok" + (f"  STALE: {bad}" if bad else ""))
        if args.verify:
            return 1 if bad else 0
        print()

        provider = OpenAIProvider(
            settings.openai_api_key,
            settings.embedding_model,
            settings.answer_model,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        vectors = await embed_all(provider, settings.embedding_model, [q["question"] for q in questions])

        variants: dict = {
            "current": variant_current,
            "none": variant_none,
            "prefix": variant_prefix,
            "trgm": variant_trgm,
            "pgbigram": variant_pgbigram,
            **build_lexical_variants(docs),
        }
        # trgm and pgbigram are opt-in: they need the throwaway database objects
        # in this module's docstring, and running them by default would crash a
        # plain `python scripts/eval_retrieval.py` on a clean database.
        wanted = (
            args.variants.split(",")
            if args.variants
            else [v for v in variants if v not in ("trgm", "pgbigram")]
        )

        store = PgVectorStore(session)
        # Dense side is identical across variants and across the knob sweep, so
        # fetch the widest list once and slice it per configuration.
        dense: dict[str, list[str]] = {}
        for entry in questions:
            hits = await store.search(vectors[entry["question"]], 100, None)
            dense[entry["id"]] = [h.chunk_id for h in hits]

        weights = [float(w) for w in args.weights.split(",")]
        configs = [(rrf_k, limit, w) for w in weights]
        if args.sweep:
            configs = [(k, n, w) for k in (10, 60) for n in (20, 50) for w in weights]

        for cfg_k, cfg_limit, cfg_w in configs:
            header = (
                f"top_n={top_n}  candidate_limit={cfg_limit}  rrf_k={cfg_k}  sparse_weight={cfg_w}"
            )
            print(f"\n{header}\n{'-' * len(header)}")
            print(
                f"{'variant':<14} {'recall@' + str(top_n):>9} {'anchor@' + str(top_n):>9} "
                f"{'prec@' + str(top_n):>9} {'overlap':>8} {'sparse-noise':>13}"
            )
            for name in wanted:
                fn = variants[name]
                recalls, precisions, overlaps, noise = [], [], [], []
                anchors = []
                for entry in questions:
                    gold = set(entry["gold_pages"])
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
                    hit, hits = score([pages.get(cid) for cid in selected], gold)
                    anchors.append(
                        anchor_hit([docs[cid] for cid in selected if cid in docs], entry["anchor"])
                    )
                    recalls.append(hit)
                    precisions.append(hits / top_n)
                    overlaps.append(len(set(dense_ids) & set(sparse_ids)))
                    # slots that only the sparse side put there AND that miss gold
                    noise.append(
                        sum(
                            1
                            for cid in selected
                            if cid not in dense_ids[:top_n] and pages.get(cid) not in gold
                        )
                    )
                    if args.show == entry["id"]:
                        print(f"  [{name}] {entry['id']}")
                        for i, cid in enumerate(selected, 1):
                            mark = "HIT " if pages.get(cid) in gold else "    "
                            print(
                                f"    {mark}{i}. page={pages.get(cid)} "
                                f"dense={dense_ids.index(cid) + 1 if cid in dense_ids else '-'} "
                                f"sparse={sparse_ids.index(cid) + 1 if cid in sparse_ids else '-'}"
                            )
                n = len(questions)
                if args.detail:
                    for entry, hits in zip(questions, precisions, strict=True):
                        print(f"    {entry['id']:<28} {round(hits * top_n)}/{top_n}")
                print(
                    f"{name:<14} {sum(recalls) / n:>9.3f} {sum(anchors) / n:>9.3f} "
                    f"{sum(precisions) / n:>9.3f} "
                    f"{sum(overlaps) / n:>8.2f} {sum(noise) / n:>13.2f}"
                )

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
