"""Measure Korean retrieval quality against scripts/eval_questions_ko.json.

    python scripts/eval_retrieval.py --verify                  # anchors only, no API calls
    python scripts/eval_retrieval.py --arm dense,sparse,hybrid # the arm decomposition
    python scripts/eval_retrieval.py --sparse current,word_bm25,kiwi_bm25
    python scripts/eval_retrieval.py --dense small,large,bgem3
    python scripts/eval_retrieval.py --expand 0,3 --rerank ""  # stage by stage

A SCRIPT, not a test: it talks to the running stack's Postgres and embeds each
question once against the live OpenAI API. Every embedding is cached on disk
keyed by (model, text), so re-running a sweep costs zero further API calls.

EVERY ROW IS ONE PIPELINE CONFIGURATION and rows are a cross product of
--arm x --dense x --sparse x --expand x --rerank x --expansion. That shape is
the point: the redesign is decided one stage at a time, and a stage that cannot
be switched off in this harness cannot be switched off in production either.

Metrics, all against the fixture:
  recall@N     - fraction of questions with at least one gold-PAGE chunk in the
                 N returned. Page-level, so it survives a re-ingestion.
  anchor@N     - fraction of questions where a chunk carrying the answer-bearing
                 SENTENCE reached the model. This is the metric that decides
                 things here; recall@N scored a hit on the owner's 공지예외
                 question for a page-594 chunk that restates the rule as a double
                 negative while the chunk on 593 that states it plainly never
                 arrived. The metric reported success and the answer was inverted.
  prec@N       - mean share of the N slots holding a gold-page chunk.
  ms/q         - mean wall-clock of the retrieval path, per question. Excludes
                 the question embedding when it is served from cache, so the
                 dense arm's own latency is reported separately by --time-embed.
  $/q          - mean marginal API cost per question, from PRICES below.

Every metric is reported per `group`, never only as an average: the groups exist
because the average hides exactly the failure class each was written for.
  base       - 21 general questions over the manual
  neighbor   - 8 written from measured proviso PAIRS (rule chunk, 다만 chunk)
  tokenizer  - probes the josa/tokenizer mismatch in both directions
  proviso    - the answer turns on a 단서/다만 clause; the main rule alone is
               a confidently WRONG answer. The anchor is the PROVISO sentence.
  crossref   - needs a 준용/참조 chain WITHIN the corpus. This is the group that
               decides whether entity-graph retrieval is the next slice.

The eval_kiwi table (--setup-kiwi / --drop-kiwi) is a THROWAWAY holding morpheme
tsvectors, so the "morpheme tokens + ts_rank" cell can be measured with the real
Postgres scorer before anyone pays for a migration. It touches no product table.
"""

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FIXTURE = Path(__file__).with_name("eval_questions_ko.json")
CACHE_DIR = Path(tempfile.gettempdir()) / "mopan-eval"

# USD per 1M tokens. Only the models this harness can actually bill are listed;
# an unknown model costs 0.0 and prints a warning rather than inventing a price.
PRICES = {
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4o": (2.50, 10.00),
    # Local. Free per query; the cost is the image and the RAM, not the token.
    "bge-m3": (0.0, 0.0),
    "": (0.0, 0.0),
}

DENSE_MODELS = {
    "small": "text-embedding-3-small",
    "large": "text-embedding-3-large",
    # text-embedding-3-* are Matryoshka models: OpenAI's `dimensions` parameter
    # is a prefix truncation followed by a renormalisation, which is what TRUNCATE
    # does below. It is here because it is the only dense upgrade that needs NO
    # MIGRATION - 1536 is the width chunks.embedding already has - so it prices
    # the cheap version of the dense swap against the expensive one.
    "large1536": "text-embedding-3-large",
    "bgem3": "BAAI/bge-m3",
    "bgem3ko": "dragonkue/BGE-m3-ko",
}
TRUNCATE = {"large1536": 1536}

# Korean is agglutinative: the same noun appears as 공지예외주장은 / 공지예외주장을 /
# 공지예외주장의, which a whitespace tokenizer sees as three unrelated tokens.
# Longest-first so 으로써 wins over 로 and the stem is not shredded twice. This is
# the CHEAP approximation of a morphological analyser and it is here to be beaten
# by kiwi, not to be shipped.
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


_kiwi = None


def kiwi_tokens(text: str) -> list[str]:
    """Content morphemes only. Josa (J*), endings (E*) and affixes (X*) are
    exactly the noise the whitespace tokenizer could not strip.

    kiwipiepy is an EVAL-ONLY import here. Whether it becomes a backend
    dependency is what this harness is measuring.
    """
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi

        _kiwi = Kiwi()
    keep = ("NN", "NP", "NR", "VV", "VA", "SL", "SH", "SN", "XR")
    return [t.form for t in _kiwi.tokenize(text) if t.tag.startswith(keep)]


class Bm25:
    """Okapi BM25 over whatever tokenizer it is handed. In-memory, 2578 docs.

    Here to separate two questions that the shipped sparse arm confounds: does
    the Korean failure come from the TOKENIZER (whitespace 어절 vs morphemes) or
    from the SCORER (ts_rank has no IDF)? Holding one fixed and varying the other
    is the whole design of the sparse table.
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

    def idf(self, term: str) -> float:
        posting = self.postings.get(term)
        if not posting:
            return 0.0
        return math.log(1 + (self.n - len(posting) + 0.5) / (len(posting) + 0.5))

    def match(self, term: str) -> list[str]:
        """Which indexed terms a query term matches. Exact, unless overridden."""
        return [term] if term in self.postings else []

    def search(self, query: str, limit: int, k1: float = 1.2, b: float = 0.75) -> list[str]:
        scores: dict[str, float] = defaultdict(float)
        for query_term in set(self.tokenize(query)):
            for term in self.match(query_term):
                idf = self.idf(term)
                for chunk_id, freq in self.postings[term]:
                    norm = 1 - b + b * self.lengths[chunk_id] / self.avgdl
                    scores[chunk_id] += idf * freq * (k1 + 1) / (freq + k1 * norm)
        # id as tie-break, same reason keyword_search sorts by id: a ranking that
        # depends on dict order makes RRF non-reproducible.
        ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
        return [chunk_id for chunk_id, _ in ranked[:limit]]


class PrefixBm25(Bm25):
    """BM25 where a josa-stripped query stem matches every indexed term it
    prefixes, each contributing ITS OWN IDF.

    This is the combination nobody had tested. Prefix matching was rejected on
    BLAST RADIUS - `'출원':*` widens 350 chunks to 1556, `'이':*` hits 1766 of
    2578 - but that is a RANKING failure, not a matching failure, and it was
    measured under ts_rank, which has no IDF. Under BM25 a term appearing in 1766
    of 2578 chunks carries idf ~0.4 while 상표등록출원이나, appearing in 2, carries
    ~7.2: the blast is admitted to the candidate set and then weighed at almost
    nothing. Whether that is enough is what the `prefix_bm25` row answers.

    A one-character stem is still refused. It is not a stem, it is a wildcard,
    and its IDF is computed per MATCHED term rather than for the wildcard, so
    nothing down-weights the union it drags in.
    """

    def __init__(self, docs, tokenize):
        super().__init__(docs, tokenize)
        self._by_prefix: dict[str, list[str]] = defaultdict(list)
        for term in self.postings:
            for size in range(2, min(len(term), 12) + 1):
                self._by_prefix[term[:size]].append(term)

    def match(self, term: str) -> list[str]:
        root = stem(term)
        if len(root) < 2:
            return [term] if term in self.postings else []
        return self._by_prefix.get(root[:12], [])


# --------------------------------------------------------------------------
# sparse arms
# --------------------------------------------------------------------------


async def sparse_current(session, query, limit):
    """The SHIPPED sparse arm: to_tsvector('simple') + ts_rank. The baseline."""
    from app.retrieval.keyword_search import keyword_search

    return await keyword_search(session, query, limit)


async def sparse_none(session, query, limit):
    return []


async def sparse_prefix(session, query, limit):
    """Josa-strip each query token, then ask tsquery for a PREFIX match.

    The character-level workaround: '공지예외주장은' -> '공지예외주장':*. Measured
    here rather than argued about, because published MIRACL numbers put
    character-boundary tricks in the 0.41 NDCG family and a real analyser in the
    0.61 family. Two statements because the stripping happens in Python between
    them; the token list goes back as a bound array, never concatenated into SQL.
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
    # A one-character prefix is a wildcard, not a stem: '이':* matches every
    # lexeme starting with 이 - measured at 1766 of 2578 chunks on this corpus.
    # Short stems are matched exactly.
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


def build_bm25_arms(docs: dict[str, str]) -> dict:
    """In-memory BM25 arms, built once over the whole corpus.

    word_bm25 vs current isolates the SCORER (same whitespace tokenizer).
    kiwi_bm25 vs word_bm25 isolates the TOKENIZER (same BM25 scorer).
    """
    indexes = {
        "word_bm25": Bm25(docs, lambda t: [w.lower() for w in _TOKEN.findall(t)]),
        "stem_bm25": Bm25(docs, lambda t: [stem(w.lower()) for w in _TOKEN.findall(t)]),
        "bigram_bm25": Bm25(docs, lambda t: ngrams(t, 2)),
        "prefix_bm25": PrefixBm25(docs, lambda t: [w.lower() for w in _TOKEN.findall(t)]),
    }
    try:
        kiwi_tokens("확인")
    except ImportError:
        print("(kiwipiepy not installed - skipping kiwi_bm25; pip install kiwipiepy)")
    else:
        indexes["kiwi_bm25"] = Bm25(docs, kiwi_tokens)

    def make(index):
        async def run(session, query, limit):
            return index.search(query, limit)

        return run

    return {name: make(index) for name, index in indexes.items()}


# --------------------------------------------------------------------------
# dense arms
# --------------------------------------------------------------------------


def _cache(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{re.sub(r'[^a-zA-Z0-9._-]', '_', name)}.json"


def _key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def embed_openai(model: str, texts: list[str], settings) -> tuple[dict[str, list[float]], int]:
    """Embed with caching. Returns (text -> vector, tokens actually billed)."""
    from app.core.tokens import count_tokens
    from app.llm.openai_provider import OpenAIProvider

    path = _cache(f"emb-{model}")
    cache = json.loads(path.read_text()) if path.exists() else {}
    missing = [t for t in dict.fromkeys(texts) if _key(t) not in cache]
    billed = 0
    if missing:
        billed = sum(count_tokens(t) for t in missing)
        print(f"  embedding {len(missing)} text(s) against {model} (~{billed} tokens)")
        provider = OpenAIProvider(
            settings.openai_api_key,
            model,
            settings.answer_model,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        for start in range(0, len(missing), 256):
            batch = missing[start : start + 256]
            for t, v in zip(batch, await provider.embed(batch), strict=True):
                cache[_key(t)] = v
        path.write_text(json.dumps(cache))
    return {t: cache[_key(t)] for t in texts}, billed


def embed_local(model: str, texts: list[str]) -> dict[str, list[float]]:
    """Embed with a local sentence-transformers model, cached on disk.

    NOT a backend dependency and deliberately not installed by anything here:
    `pip install sentence-transformers` locally to answer "is bge-m3 worth a
    model server?" with a number instead of an argument.
    """
    path = _cache(f"emb-{model}")
    cache = json.loads(path.read_text()) if path.exists() else {}
    missing = [t for t in dict.fromkeys(texts) if _key(t) not in cache]
    if missing:
        from sentence_transformers import SentenceTransformer

        print(f"  embedding {len(missing)} text(s) with local {model} (CPU, this is slow)")
        encoder = SentenceTransformer(model)
        vectors = encoder.encode(
            missing, batch_size=8, normalize_embeddings=True, show_progress_bar=True
        )
        for t, v in zip(missing, vectors, strict=True):
            cache[_key(t)] = [float(x) for x in v]
        path.write_text(json.dumps(cache))
    return {t: cache[_key(t)] for t in texts}


# A Korean sentence ends at 다./까?/요. or an enumerator boundary. Splitting on
# bare [.?!] alone shreds 제52조제2항 and 2017. 11. 1. into fragments, which is why
# the enders are matched as SYLLABLE + period rather than as period.
_SENTENCE = re.compile(r"(?<=[가-힣][.?!])\s+|(?<=[.?!])\n+|\n{2,}")
# Enumerated items in this manual are their own propositions - "1. ...", "① ...",
# "가. ..." - and a window that lumps five of them averages five meanings into one
# vector. They are cut as units.
_ENUM = re.compile(r"(?=(?:^|\n)\s*(?:[0-9]{1,2}\.|[①-⑳]|[가-힣]\.)\s)")
MIN_UNIT_CHARS = 20


def sentence_units(rows) -> tuple[list[str], dict[str, list[str]]]:
    """Split every chunk into sentence-sized units. Returns (unit texts, unit ->
    parent chunk ids).

    SMALL-TO-BIG: the unit is what gets embedded and matched, the parent chunk is
    what reaches the model. Precision comes from the small unit, context from the
    big one, and `neighbors.expand` still widens the parent afterwards - so this
    changes the granularity of MATCHING only, never of what is cited.

    Units are DEDUPLICATED across chunks on purpose. CHUNK_OVERLAP=150 repeats
    the previous chunk's tail, so 2242 of 2577 adjacent pairs share text; without
    the dedupe the same sentence would be embedded twice, billed twice, and would
    occupy two slots of one ranked list. A unit that genuinely appears in two
    chunks keeps BOTH parents, so whichever window the reader needs is reachable.
    """
    parents: dict[str, list[str]] = {}
    order: list[str] = []
    for row in rows:
        for part in _SENTENCE.split(row.content):
            for unit in _ENUM.split(part):
                unit = unit.strip()
                if len(unit) < MIN_UNIT_CHARS:
                    continue
                if unit not in parents:
                    parents[unit] = []
                    order.append(unit)
                parents[unit].append(str(row.id))
    return order, parents


class DenseIndex:
    """Exact cosine over the whole corpus, in memory.

    Exact rather than HNSW so that a dense-arm comparison measures the MODEL and
    not two different approximate indexes. At 2578 x 1536 that is 16 MB and a
    single matrix multiply; the sanity row `small` against the shipped
    PgVectorStore confirms the two agree, which is what makes this substitution
    legitimate rather than convenient.
    """

    def __init__(self, ids: list[str], vectors):
        import numpy as np

        self.ids = ids
        matrix = np.asarray(vectors, dtype="float32")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        self.matrix = matrix / np.maximum(norms, 1e-12)

    def search(self, vector, limit: int) -> list[str]:
        import numpy as np

        query = np.asarray(vector, dtype="float32")
        query = query / max(float(np.linalg.norm(query)), 1e-12)
        scores = self.matrix @ query
        take = min(limit, len(scores) - 1)
        top = np.argpartition(-scores, take)[: take + 1]
        top = top[np.argsort(-scores[top])]
        return [self.ids[i] for i in top]


class SentenceIndex(DenseIndex):
    """Small-to-big: matches sentences, returns their parent chunks.

    Over-fetches by OVERFETCH because several top sentences routinely share one
    parent - that is the whole point of the unit being smaller - so a raw
    top-`limit` of sentences collapses to far fewer chunks. The de-duplication
    keeps FIRST occurrence, so a chunk's rank is the rank of its best sentence.
    """

    OVERFETCH = 6

    def __init__(self, units: list[str], vectors, parents: dict[str, list[str]]):
        super().__init__(units, vectors)
        self.parents = parents

    def search(self, vector, limit: int) -> list[str]:
        chunk_ids: list[str] = []
        for unit in super().search(vector, min(limit * self.OVERFETCH, len(self.ids))):
            chunk_ids.extend(self.parents[unit])
        return list(dict.fromkeys(chunk_ids))[:limit]


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


def _ranks(ids: list[str]) -> dict[str, int]:
    """1-based rank per id, FIRST occurrence winning - the same rule
    reciprocal_rank_fusion and app/retrieval/service.py use, so the ranks fed to
    the clarification detector here are the ranks the product would record."""
    return {chunk_id: rank for rank, chunk_id in enumerate(dict.fromkeys(ids), start=1)}


def score(returned_pages: list[int | None], gold: set[int]) -> tuple[int, int]:
    hits = sum(1 for page in returned_pages if page in gold)
    return (1 if hits else 0), hits


def anchor_hit(returned_contents: list[str], anchor: str) -> int:
    """Did a chunk carrying the answer-bearing sentence actually reach the model?"""
    return 1 if any(anchor in content for content in returned_contents) else 0


async def expand_selection(session, selected, meta, *, mode, settings, query):
    """The selected ids as RetrievedChunks, with neighbour expansion applied.

    Calls the SHIPPED `app.retrieval.neighbors.expand` - the same function
    hybrid_search calls, with the same CHUNK_OVERLAP and the same token budget -
    so what this measures is the product and not a second implementation of it.
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


# The tokenizers that can be shipped INTO a tsvector column. Each is a pure
# function from text to a token list, used identically at ingest and at query
# time - which is the whole contract a lexical index has. `kiwi` is the only one
# carrying a dependency; that is what its row has to earn.
LEX_TOKENIZERS = {
    "stem": lambda t: [stem(w.lower()) for w in _TOKEN.findall(t)],
    "bigram": lambda t: ngrams(t, 2),
    "kiwi": kiwi_tokens,
}


def sparse_lex(name: str):
    """Morpheme/stem/bigram tokens + the REAL Postgres ts_rank scorer, over a
    throwaway eval_lex_<name> table.

    This is the cell the in-memory BM25 rows cannot answer. kiwi_bm25 changes the
    tokenizer AND the scorer at once, so a gain there cannot be attributed, and -
    more to the point - a BM25 scorer is not something this deployment can
    actually run: the Postgres image ships neither pg_search nor VectorChord-BM25
    (checked: pg_available_extensions lists only pg_trgm, unaccent, vector). So
    ts_rank is the scorer that would really ship, and these rows price the
    migration that is actually on the table rather than the one that is not.
    """

    async def run(session, query, limit):
        from sqlalchemy import ARRAY, Text, bindparam, text

        terms = sorted(set(LEX_TOKENIZERS[name](query)))
        if not terms:
            return []
        rows = await session.scalars(
            text(
                f"""WITH q AS (SELECT to_tsquery('simple',
                       (SELECT string_agg(quote_literal(t), ' | ') FROM unnest(:terms) t)) AS tq)
                   SELECT k.id FROM eval_lex_{name} k, q
                    WHERE k.tsv @@ q.tq
                    ORDER BY ts_rank(k.tsv, q.tq) DESC, k.id
                    LIMIT :lim"""
            ).bindparams(
                bindparam("terms", value=terms, type_=ARRAY(Text)),
                bindparam("lim", value=limit),
            )
        )
        return [str(cid) for cid in rows]

    return run


async def setup_lex(session, name: str, docs: dict[str, str]) -> None:
    """Build a throwaway lexical index. Product tables are never touched.

    The table name is interpolated because it comes from LEX_TOKENIZERS, a
    literal dict in this file, and never from input. Every VALUE is bound.
    """
    from sqlalchemy import text

    tokenize = LEX_TOKENIZERS[name]
    table = f"eval_lex_{name}"
    await session.execute(text(f"DROP TABLE IF EXISTS {table}"))
    await session.execute(text(f"CREATE TABLE {table} (id uuid primary key, tsv tsvector)"))
    print(f"  tokenising {len(docs)} chunks with {name} ...")
    rows = [{"i": cid, "t": " ".join(tokenize(body))} for cid, body in docs.items()]
    for start in range(0, len(rows), 500):
        await session.execute(
            text(f"INSERT INTO {table} (id, tsv) VALUES (:i, to_tsvector('simple', :t))"),
            rows[start : start + 500],
        )
    await session.execute(text(f"CREATE INDEX {table}_gin ON {table} USING gin(tsv)"))
    await session.commit()
    n = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
    print(f"  {table}: {n} rows. Drop with --drop-lex when done.")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", default="hybrid", help="dense,sparse,hybrid")
    parser.add_argument(
        "--unit", default="chunk", help="dense matching granularity: chunk,sentence"
    )
    parser.add_argument("--dense", default="small", help=f"{','.join(DENSE_MODELS)} or none")
    parser.add_argument("--sparse", default="current", help="current,none,prefix,kiwi_ts,*_bm25")
    parser.add_argument("--expand", default="0", help="extra LLM-written queries, comma-separated")
    parser.add_argument("--rerank", default="", help="rerank model names, comma-separated; '' = off")
    parser.add_argument("--expansion", default=None, help="neighbour modes: off,targeted,blanket")
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--rrf-k", type=int, default=None)
    parser.add_argument("--weights", default=None, help="sparse weight(s) in RRF")
    parser.add_argument("--pgvector", action="store_true", help="sanity row: shipped HNSW arm")
    parser.add_argument("--verify", action="store_true", help="anchors only; no API calls")
    parser.add_argument("--setup-lex", default="", help="build eval_lex_<name> tables")
    parser.add_argument("--drop-lex", action="store_true", help="drop every eval_lex_* table")
    parser.add_argument("--show", default="", help="question id to print per-slot detail for")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings
    from app.core.tokens import count_tokens
    from app.retrieval.rrf import reciprocal_rank_fusion

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    questions = fixture["questions"]
    settings = get_settings()
    top_n = args.top_n or settings.retrieval_top_n
    limit = args.limit or settings.retrieval_candidate_limit
    rrf_k = args.rrf_k if args.rrf_k is not None else settings.rrf_k
    weights = [float(w) for w in (args.weights or str(settings.sparse_weight)).split(",")]
    modes = [m.strip() for m in (args.expansion or settings.neighbor_expansion).split(",")]

    engine = create_async_engine(settings.database_url.replace("@postgres:", "@127.0.0.1:"))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        if args.drop_lex:
            for name in [*LEX_TOKENIZERS, "kiwi_legacy"]:
                await session.execute(text(f"DROP TABLE IF EXISTS eval_lex_{name}"))
            await session.execute(text("DROP TABLE IF EXISTS eval_kiwi"))
            await session.commit()
            print("eval_lex_* dropped")
            await engine.dispose()
            return 0

        from app.models.chunk import Chunk
        from app.models.document import Document

        rows = (
            await session.execute(
                select(
                    Chunk.id, Chunk.page, Chunk.content, Chunk.document_id,
                    Chunk.chunk_index, Chunk.section,
                )
                .join(Document, Document.id == Chunk.document_id)
                .where(Document.filename == fixture["document_filename"])
            )
        ).all()
        if not rows:
            print(f"no chunks for {fixture['document_filename']!r} - is the corpus ingested?")
            return 1
        pages = {str(r.id): r.page for r in rows}
        docs = {str(r.id): r.content for r in rows}
        meta = {str(r.id): r for r in rows}
        print(f"corpus: {len(rows)} chunks, {len(set(pages.values()))} pages")

        # Anchors are the fixture's own regression check: if extraction changes
        # and a gold page no longer carries the passage, every number below is a
        # measurement of the wrong thing.
        bad = [
            e["id"]
            for e in questions
            if not any(e["anchor"] in c for cid, c in docs.items() if pages[cid] in set(e["gold_pages"]))
        ]
        groups = list(dict.fromkeys(e.get("group", "base") for e in questions))
        counts = Counter(e.get("group", "base") for e in questions)
        print(
            f"anchor check: {len(questions) - len(bad)}/{len(questions)} ok"
            + (f"  STALE: {bad}" if bad else "")
        )
        print("groups: " + ", ".join(f"{g}={counts[g]}" for g in groups))
        if args.verify:
            await engine.dispose()
            return 1 if bad else 0

        for name in [n for n in args.setup_lex.split(",") if n]:
            await setup_lex(session, name, docs)

        # --- dense arms -------------------------------------------------
        chunk_ids = [str(r.id) for r in rows]
        chunk_texts = [r.content for r in rows]
        questions_text = [e["question"] for e in questions]
        units = [u for u in args.unit.split(",") if u]
        sent_texts, sent_parents = sentence_units(rows)
        if "sentence" in units:
            print(
                f"sentence units: {len(sent_texts)} from {len(rows)} chunks, "
                f"mean {sum(len(t) for t in sent_texts) / len(sent_texts):.0f} chars "
                f"(chunks mean {sum(len(t) for t in chunk_texts) / len(chunk_texts):.0f})"
            )
        dense_names = [d for d in args.dense.split(",") if d and d != "none"]
        dense_indexes: dict[str, DenseIndex] = {}
        dense_vectors: dict[str, dict[str, list[float]]] = {}
        query_cost: dict[str, float] = {}
        for name in dense_names:
            model = DENSE_MODELS[name]
            rate = PRICES.get(model, (0.0, 0.0))[0]
            for unit in units:
                key = f"{name}:{unit}"
                body = chunk_texts if unit == "chunk" else sent_texts
                print(f"dense arm {key} ({model}, {len(body)} vectors):")
                if name in ("bgem3", "bgem3ko"):
                    vecs = embed_local(model, body + questions_text)
                elif name == "small" and unit == "chunk" and model == settings.embedding_model:
                    # The corpus side is ALREADY EMBEDDED with this model and
                    # sitting in chunks.embedding. Re-embedding it to compare
                    # against a candidate would bill for vectors the product
                    # already paid for, and would compare a fresh corpus against
                    # a stored one. Only the questions are embedded, and cached.
                    stored = {
                        str(r.id): list(r.embedding)
                        for r in (await session.execute(select(Chunk.id, Chunk.embedding))).all()
                        if r.embedding is not None
                    }
                    qvecs, _ = await embed_openai(model, questions_text, settings)
                    vecs = {
                        **{r.content: stored[str(r.id)] for r in rows if str(r.id) in stored},
                        **qvecs,
                    }
                else:
                    vecs, _ = await embed_openai(model, body + questions_text, settings)
                if name in TRUNCATE:
                    vecs = {t: v[: TRUNCATE[name]] for t, v in vecs.items()}
                if unit == "chunk":
                    dense_indexes[key] = DenseIndex(chunk_ids, [vecs[t] for t in chunk_texts])
                else:
                    dense_indexes[key] = SentenceIndex(
                        sent_texts, [vecs[t] for t in sent_texts], sent_parents
                    )
                dense_vectors[key] = {q: vecs[q] for q in questions_text}
                corpus = sum(count_tokens(t) for t in body) / 1e6 * rate
                query_cost[key] = (
                    sum(count_tokens(q) for q in questions_text) / len(questions) / 1e6 * rate
                )
                print(
                    f"  corpus embed ${corpus:.4f} one-off, query ${query_cost[key]:.8f}/q"
                )
        dense_keys = [f"{n}:{u}" for n in dense_names for u in units]

        # --- sparse arms ------------------------------------------------
        sparse_arms: dict = {
            "current": sparse_current,
            "none": sparse_none,
            "prefix": sparse_prefix,
            **{f"{name}_ts": sparse_lex(name) for name in LEX_TOKENIZERS},
        }
        if any(n.endswith("_bm25") for n in args.sparse.split(",")):
            sparse_arms.update(build_bm25_arms(docs))
        sparse_names = [s for s in args.sparse.split(",") if s]

        # --- stages -----------------------------------------------------
        expands = [int(x) for x in args.expand.split(",") if x != ""]
        reranks = args.rerank.split(",")
        arms = [a for a in args.arm.split(",") if a]

        header = (
            f"{'row':<40} {'group':<10} {'n':>3} {'recall':>7} {'anchor':>7} "
            f"{'prec':>6} {'ms/q':>8} {'$/q':>10} {'clarify':>8}"
        )

        from app.llm.openai_provider import OpenAIProvider

        provider = OpenAIProvider(
            settings.openai_api_key,
            settings.embedding_model,
            settings.answer_model,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

        print(f"\ntop_n={top_n} candidate_limit={limit} rrf_k={rrf_k}")
        print(f"\n{header}\n{'-' * len(header)}")

        for arm in arms:
            for dense_name in (dense_keys or ["none"]) if arm != "sparse" else ["none"]:
                for sparse_name in sparse_names if arm != "dense" else ["none"]:
                    for weight in weights:
                        for n_expand in expands:
                            for rerank_model in reranks:
                                for mode in modes:
                                    label = f"{arm}/{dense_name}/{sparse_name}"
                                    if weight != 1.0:
                                        label += f"/w{weight}"
                                    if n_expand:
                                        label += f"/x{n_expand}"
                                    if rerank_model:
                                        label += f"/rr"
                                    label += f"/{mode}"
                                    await measure_row(
                                        label, session, provider, settings, questions, groups,
                                        pages, docs, meta, dense_indexes, dense_vectors,
                                        sparse_arms, arm=arm, dense_name=dense_name,
                                        sparse_name=sparse_name, weight=weight, n_expand=n_expand,
                                        rerank_model=rerank_model, mode=mode, top_n=top_n,
                                        limit=limit, rrf_k=rrf_k, show=args.show,
                                        query_cost=query_cost.get(dense_name, 0.0),
                                        fuse=reciprocal_rank_fusion,
                                    )

        if args.pgvector and dense_keys:
            await sanity_pgvector(session, questions, dense_vectors[dense_keys[0]],
                                  dense_indexes[dense_keys[0]], limit)

    await engine.dispose()
    return 0


async def sanity_pgvector(session, questions, vectors, index, limit) -> None:
    """Does the in-memory exact index agree with the shipped HNSW one?

    Printed rather than asserted: a small disagreement at the tail is expected
    from an approximate index, and the number is what says whether substituting
    the in-memory index for the model comparison was legitimate.
    """
    from app.retrieval.vector_store import PgVectorStore

    store = PgVectorStore(session)
    overlaps = []
    for entry in questions:
        hits = await store.search(vectors[entry["question"]], limit, None)
        pg = [h.chunk_id for h in hits]
        mem = index.search(vectors[entry["question"]], limit)
        overlaps.append(len(set(pg) & set(mem)) / limit)
    print(f"\npgvector HNSW vs in-memory exact: {sum(overlaps) / len(overlaps):.3f} overlap@{limit}")


async def measure_row(
    label, session, provider, settings, questions, groups, pages, docs, meta,
    dense_indexes, dense_vectors, sparse_arms, *, arm, dense_name, sparse_name,
    weight, n_expand, rerank_model, mode, top_n, limit, rrf_k, show, query_cost, fuse,
) -> None:
    from app.core.tokens import count_tokens

    per_group = defaultdict(lambda: defaultdict(list))
    for entry in questions:
        query = entry["question"]
        started = time.perf_counter()
        cost = 0.0

        queries = [query]
        if n_expand:
            from app.retrieval import expansion

            queries += await expansion.expand_query(
                provider, query, n_expand,
                model=settings.query_expansion_model,
                timeout=settings.query_expansion_timeout_seconds,
            )
            cost += expansion.last_cost_usd()

        rankings: list[list[str]] = []
        row_weights: list[float] = []
        for variant in queries:
            if arm in ("dense", "hybrid") and dense_name != "none":
                vector = dense_vectors[dense_name].get(variant)
                if vector is None:
                    vector = (await _embed_one(dense_name, variant, settings))
                rankings.append(dense_indexes[dense_name].search(vector, limit))
                row_weights.append(1.0)
                cost += query_cost
            if arm in ("sparse", "hybrid") and sparse_name != "none":
                rankings.append(await sparse_arms[sparse_name](session, variant, limit))
                row_weights.append(weight)

        fused = fuse(rankings, k=rrf_k, weights=row_weights)[:limit] if rankings else []
        selected = [cid for cid, _ in fused]
        fused_scores = dict(fused)
        # rankings[0]/[1] are the ORIGINAL query's dense and sparse lists by
        # construction, which is what the product records in its trace too.
        vector_rank = _ranks(rankings[0]) if arm != "sparse" and rankings else {}
        keyword_rank = _ranks(rankings[1] if arm == "hybrid" else rankings[0]) if arm != "dense" and rankings else {}

        if rerank_model:
            from app.retrieval.reranker import LLMReranker
            from app.retrieval.evidence import RetrievedChunk

            candidates = [
                RetrievedChunk(chunk_id=c, document_id=str(meta[c].document_id), filename="",
                               content=meta[c].content, page=meta[c].page,
                               section=meta[c].section, chunk_index=meta[c].chunk_index)
                for c in selected if c in meta
            ]
            reranker = LLMReranker(provider, rerank_model, settings.rerank_timeout_seconds)
            candidates = await reranker.rerank(query, candidates)
            cost += getattr(reranker, "last_cost", 0.0)
            selected = [c.chunk_id for c in candidates]

        selected = selected[:top_n]
        chunks = await expand_selection(session, selected, meta, mode=mode,
                                        settings=settings, query=query)
        elapsed = (time.perf_counter() - started) * 1000

        gold = set(entry["gold_pages"])
        hit, hits = score([pages.get(c) for c in selected], gold)
        bucket = per_group[entry.get("group", "base")]
        bucket["recall"].append(hit)
        bucket["anchor"].append(anchor_hit([c.content for c in chunks], entry["anchor"]))
        bucket["prec"].append(hits / top_n)
        bucket["ms"].append(elapsed)
        bucket["cost"].append(cost)
        bucket["tokens"].append(sum(count_tokens(c.content) for c in chunks))
        # FALSE-TRIGGER RATE for the weak-evidence clarification branch. Every
        # question in this fixture is well-formed and answerable from the corpus,
        # so every trigger here is a user who would be interrogated instead of
        # answered. That is the number that decides whether the branch ships, and
        # it is measured on the same run as the recall numbers rather than
        # argued about separately.
        from app.chat.service import evidence_is_weak
        from app.retrieval.evidence import chunk_to_evidence

        for chunk, cid in zip(chunks, selected, strict=False):
            chunk.rrf_score = fused_scores.get(cid, 0.0)
            chunk.vector_rank = vector_rank.get(cid)
            chunk.keyword_rank = keyword_rank.get(cid)
        bucket["clarify"].append(
            1
            if evidence_is_weak(
                [chunk_to_evidence(c) for c in chunks],
                min_rrf_score=settings.weak_evidence_rrf_score,
            )
            else 0
        )
        if show == entry["id"]:
            print(f"  [{label}] {entry['id']}")
            for i, cid in enumerate(selected, 1):
                mark = "HIT " if pages.get(cid) in gold else "    "
                print(f"    {mark}{i}. page={pages.get(cid)} idx={meta[cid].chunk_index}")

    total = defaultdict(list)
    for bucket in per_group.values():
        for key, values in bucket.items():
            total[key].extend(values)
    for group in [*groups, "ALL"]:
        bucket = total if group == "ALL" else per_group.get(group)
        if not bucket:
            continue
        n = len(bucket["recall"])
        mean = lambda key: sum(bucket[key]) / n  # noqa: E731
        print(
            f"{label:<40} {group:<10} {n:>3} {mean('recall'):>7.3f} {mean('anchor'):>7.3f} "
            f"{mean('prec'):>6.3f} {mean('ms'):>8.1f} {mean('cost'):>10.6f} "
            f"{sum(bucket['clarify']):>3}/{n:<4}"
        )


async def _embed_one(dense_name, text_, settings):
    """An expansion variant is a query nobody embedded up front."""
    model = DENSE_MODELS[dense_name]
    if dense_name in ("bgem3", "bgem3ko"):
        return embed_local(model, [text_])[text_]
    vectors, _ = await embed_openai(model, [text_], settings)
    return vectors[text_]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
