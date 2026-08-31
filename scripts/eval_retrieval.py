"""Measure Korean retrieval quality against scripts/eval_questions_ko.json.

    python scripts/eval_retrieval.py                      # every variant, default knobs
    python scripts/eval_retrieval.py --variants current,none
    python scripts/eval_retrieval.py --sweep              # rrf_k / candidate_limit grid
    python scripts/eval_retrieval.py --verify             # only check the fixture's anchors
    python scripts/eval_retrieval.py --variants current --expansion off,targeted,blanket

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
what this measures is retrieval, not reranking. That is not a simplification of
the product: NoneReranker is what every shipped call site passes.

`--expansion` runs the SHIPPED `app.retrieval.neighbors.expand` over the selected
ids, not a re-implementation, so what it measures is the product. It changes only
`anchor@N` and `tokens` - expansion adds text to slots that were already won, so
`recall@N` and `precision@N`, which are page-level and slot-level, cannot move.

Questions carry an optional `group`, and every metric is reported per group. The
21 original questions are group "base"; "neighbor" is the set written FROM the
measured proviso splits, where the answer requires the sentence in the NEXT
chunk. A change to expansion that shows nothing on "base" and everything on
"neighbor" is the expected shape, not a bug in the measurement.

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


def make_prefix(min_len: int):
    """Josa-strip each query token, then ask tsquery for a PREFIX match.

    '공지예외주장은' -> '공지예외주장':* , which the index answers with every
    lexeme that starts with the stem - 공지예외주장은/을/의 all match. Two
    statements because the stripping happens in Python between them; the token
    list goes back as a bound array, never concatenated into SQL.

    `min_len` is the gate, and it is the whole question this variant exists to
    answer: a stem shorter than it is a wildcard, not a stem - '이':* matched 1766
    of 2578 chunks and '출원':* 1556 - so the gate is what separates "recall" from
    "the corpus". Swept, not picked: run --prefix-min 2,3,4,5,6.

    Below the gate the ORIGINAL lexeme is matched exactly, not the stem. Matching
    the bare stem there is strictly worse than today: '역병은' stems to '역병',
    which as an exact term no longer matches the '역병은' the document wrote, so
    the strip would have removed a match that the unchanged code already had.
    Stripping only earns its keep when the ':*' that follows it can pay it back.
    """

    async def variant_prefix(session, query, limit):
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
        prefixed, exact = set(), set()
        for lex in lexemes:
            s = stem(lex)
            (prefixed if len(s) >= min_len else exact).add(s if len(s) >= min_len else lex)
        if not prefixed and not exact:
            return []
        ts = text(
            """to_tsquery('simple', (SELECT string_agg(t, ' | ') FROM (
                   SELECT quote_literal(s) || ':*' AS t FROM unnest(:pre) s
                   UNION ALL SELECT quote_literal(s) FROM unnest(:exa) s) u))"""
        ).bindparams(
            bindparam("pre", value=sorted(prefixed), type_=ARRAY(Text)),
            bindparam("exa", value=sorted(exact), type_=ARRAY(Text)),
        )
        query_ = (
            select(Chunk.id)
            .where(Chunk.content_tsv.op("@@", is_comparison=True)(ts))
            .order_by(func.ts_rank(Chunk.content_tsv, ts).desc(), Chunk.id)
            .limit(limit)
        )
        return [str(cid) for cid in await session.scalars(query_)]

    return variant_prefix


def make_trgm_gated(threshold: float):
    """trgm, but behind the SAME stopword filter the shipped sparse arm uses.

    make_trgm() measures pg_trgm as a REPLACEMENT for the sparse arm, and a
    replacement loses the abstention: word_similarity has no notion of a stopword,
    so 'how does it?' scores a real extent against the chunk made of those words
    and puts pure noise at sparse rank 1 - the exact displacement the shipped
    tsquery abstains to avoid, and what three tests pin. This variant keeps the
    filter in front and abstains when nothing survives it, which is what a
    shippable pg_trgm would have to do. Whether the filtered lexemes are still a
    good trigram probe is the thing to measure: word_similarity scores the FIRST
    argument as one string, and joining surviving lexemes with spaces makes a
    string no document contains a continuous extent of.
    """

    async def variant(session, query, limit):
        from sqlalchemy import ARRAY, Text, bindparam, text

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
        if not lexemes:
            return []
        return await make_trgm(threshold)(session, " ".join(sorted(lexemes)), limit)

    return variant


def make_trgm(threshold: float):
    """pg_trgm word_similarity: best-matching substring extent, not whole-string.

    similarity() would be hopeless here - a 40-char question against a 900-char
    chunk scores near zero however good the match is.

    `threshold` is this variant's fitted constant, and it gets swept for the same
    reason the prefix gate does: --trgm-min 0.2,0.3,0.4,0.5.
    """

    async def variant_trgm(session, query, limit):
        from sqlalchemy import bindparam, text

        rows = await session.scalars(
            text(
                """SELECT id FROM chunks
                    WHERE word_similarity(:q, content) > :thr
                    ORDER BY word_similarity(:q, content) DESC, id
                    LIMIT :lim"""
            ).bindparams(
                bindparam("q", value=query),
                bindparam("thr", value=threshold),
                bindparam("lim", value=limit),
            )
        )
        return [str(cid) for cid in rows]

    return variant_trgm


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


async def measure_orchestrator(
    maker, settings, provider, questions, pages, docs, dense, top_n, limit, rrf_k
) -> None:
    """Slice 3's Super Agent on the same questions, against the same corpus.

    It runs the SHIPPED code - `plan()` then `WorkflowRun`, the same objects
    /api/chat builds - rather than a re-implementation, because a re-implementation
    would measure the eval script's idea of 슈퍼 에이전트 and not the product's.
    Since Slice 6 the planner emits a WORKFLOW GRAPH and there is one executor, so
    what this measures is the same class a saved 워크플로우 runs through.
    Tool nodes are excluded from the numbers: a tool result has no chunk id and no
    page, so it can neither hit nor miss a gold page, and counting it would
    silently penalise a graph for reaching outside the corpus.

    A question whose plan is REFUSED or EMPTY falls back to the direct path here
    exactly as it does in the router, because that is what a user gets. Reporting
    the orchestrator's number over only the questions it planned successfully
    would be reporting a system nobody runs.
    """
    from app.retrieval.keyword_search import keyword_search
    from app.retrieval.reranker import NoneReranker
    from app.retrieval.rrf import reciprocal_rank_fusion
    from app.workflow.catalogue import load_available
    from app.workflow.executor import WorkflowRun
    from app.workflow.graph import GraphError
    from app.workflow.planner import plan as make_plan

    async with maker() as session:
        resources = await load_available(session)
    print(
        f"\norchestrator: {len(resources.collections)} collection(s), "
        f"{len(resources.tools)} tool(s) in the catalogue"
    )

    async def direct(entry) -> list[str]:
        async with maker() as session:
            sparse_ids = await keyword_search(session, entry["question"], limit)
        fused = reciprocal_rank_fusion([dense[entry["id"]][:limit], sparse_ids], k=rrf_k)
        return [chunk_id for chunk_id, _ in fused[:top_n]]

    rows: dict[str, list[list[str]]] = {"direct": [], "orchestrator": []}
    fell_back = 0
    refused = 0
    step_counts: list[int] = []
    for entry in questions:
        rows["direct"].append(await direct(entry))
        try:
            graph = await make_plan(
                entry["question"], resources, llm_provider=provider, settings=settings
            )
        except GraphError as exc:
            refused += 1
            fell_back += 1
            print(f"  {entry['id']}: graph refused ({exc}) -> direct")
            rows["orchestrator"].append(rows["direct"][-1])
            continue
        step_counts.append(len(graph.tool_nodes()))
        if not graph.tool_nodes():
            fell_back += 1
            rows["orchestrator"].append(rows["direct"][-1])
            continue
        run = WorkflowRun(
            graph,
            resources,
            question=entry["question"],
            settings=settings,
            llm_provider=provider,
            sessionmaker=maker,
            reranker=NoneReranker(),
        )
        async for _frame in run.stream():
            pass
        selected = [
            item.metadata.get("chunk_id")
            for item in run.evidence()
            if item.source_type == "rag" and item.metadata.get("chunk_id")
        ][:top_n]
        if not selected:
            fell_back += 1
            selected = rows["direct"][-1]
        rows["orchestrator"].append(selected)

    n = len(questions)
    mean_steps = sum(step_counts) / len(step_counts) if step_counts else 0
    print(
        f"plans: {n - refused}/{n} accepted, {refused} refused, {fell_back} fell back to direct, "
        f"{mean_steps:.2f} steps/plan"
    )
    header = f"{'path':<14} {'recall@' + str(top_n):>9} {'anchor@' + str(top_n):>9} {'prec@' + str(top_n):>9}"
    print(f"\n{header}\n{'-' * len(header)}")
    for name, selections in rows.items():
        recalls, anchors, precisions = [], [], []
        for entry, selected in zip(questions, selections, strict=True):
            gold = set(entry["gold_pages"])
            hit, hits = score([pages.get(cid) for cid in selected], gold)
            recalls.append(hit)
            anchors.append(anchor_hit([docs[cid] for cid in selected if cid in docs], entry["anchor"]))
            precisions.append(hits / top_n)
        print(
            f"{name:<14} {sum(recalls) / n:>9.3f} {sum(anchors) / n:>9.3f} {sum(precisions) / n:>9.3f}"
        )


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
    parser.add_argument(
        "--expansion",
        default="off",
        help="comma-separated NEIGHBOR_EXPANSION modes to compare: off, targeted, blanket.",
    )
    parser.add_argument(
        "--orchestrator",
        action="store_true",
        help="also measure Slice 3's Super Agent on the same questions. ONE planner "
        "call per question against the live API, plus one embedding per plan step, "
        "so it is the only mode here that is not free to re-run.",
    )
    parser.add_argument(
        "--prefix-min",
        default="2,3,4,5,6",
        help="comma-separated minimum stem lengths for the prefix variants: a stem "
        "shorter than this is matched exactly, one at least this long as ':*'.",
    )
    parser.add_argument(
        "--trgm-min",
        default="0.2,0.3,0.4,0.5",
        help="comma-separated word_similarity thresholds for the trgm variants.",
    )
    parser.add_argument("--detail", action="store_true", help="per-question hit counts")
    parser.add_argument("--show", default="", help="question id to print per-slot detail for")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings
    from app.core.tokens import count_tokens
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
                select(
                    Chunk.id,
                    Chunk.page,
                    Chunk.content,
                    Chunk.document_id,
                    Chunk.chunk_index,
                    Chunk.section,
                )
                .join(Document, Document.id == Chunk.document_id)
                .where(Document.filename == fixture["document_filename"])
            )
        ).all()
        if not rows:
            print(f"no chunks for {fixture['document_filename']!r} - is the corpus ingested?")
            return 1
        pages = {str(row.id): row.page for row in rows}
        docs = {str(row.id): row.content for row in rows}
        # document_id and chunk_index are what neighbour expansion addresses a
        # neighbour BY, so the whole row is kept, not just its text.
        meta = {str(row.id): row for row in rows}
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
            **{
                f"prefix{n}": make_prefix(int(n))
                for n in args.prefix_min.split(",")
                if n.strip()
            },
            **{
                name: make(float(t))
                for t in args.trgm_min.split(",")
                if t.strip()
                for name, make in (
                    (f"trgm{t.strip()}", make_trgm),
                    (f"trgmgated{t.strip()}", make_trgm_gated),
                )
            },
            "pgbigram": variant_pgbigram,
            **build_lexical_variants(docs),
        }
        # trgm and pgbigram are opt-in: they need the throwaway database objects
        # in this module's docstring, and running them by default would crash a
        # plain `python scripts/eval_retrieval.py` on a clean database.
        wanted = (
            args.variants.split(",")
            if args.variants
            else [v for v in variants if not v.startswith("trgm") and v != "pgbigram"]
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

        if args.orchestrator:
            await measure_orchestrator(
                maker, settings, provider, questions, pages, docs, dense, top_n, limit, rrf_k
            )

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
