import uuid

from sqlalchemy import ARRAY, Text, bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document
from app.retrieval.tokenize import tokenize

# 'simple' MUST match the regconfig content_tsv was written with (see
# app/models/chunk.py) AND the tokenizer must match the one that wrote it. The
# failure mode is worse than a slow query and was measured, not assumed: the
# index still gets used, it just answers wrong. content_tsv stores what 'simple'
# produced ('tomatoes', 'blighted'), while plainto_tsquery('english', ...) asks
# for the stems ('tomato', 'blight'), which that row never stored - a silent
# false negative on every inflected word, with a healthy-looking Bitmap Index
# Scan in the plan. Measured on a seeded corpus, 'english' against this column
# hit 0 of 13 questions.
#
# Since 0012 the same trap exists one level up, on the TOKENIZER: `tokenizer`
# here must be the tokenizer that wrote the rows. A bigram query against
# simple-tokenized rows retrieves nothing, and vice versa. That is why
# `settings.sparse_tokenizer` and scripts/backfill_tsv.py move together, and why
# both sides call app/retrieval/tokenize.py rather than each rolling their own.
#
# So the query is built out of the SAME lexemes the column holds, then OR-joined.
# plainto_tsquery (and websearch_to_tsquery, which was measured identically) ANDs
# every term, so "How does tomato blight spread?" asked for
# 'how' & 'does' & 'tomato' & 'blight' & 'spread' and matched nothing at all -
# 1 of 13 questions retrieved anything, and the dense half silently covered.
#
# 'english' appears here only as a STOPWORD ORACLE, never as the query config:
# a word the english dictionary throws away ('how', 'does', 'is') is dropped
# from the OR. Without that filter recall is the same but ranking is not -
# ts_rank has no IDF, so a chunk repeating "How does this work?" outranks the
# real answer on 'how' | 'does' alone. Korean words are unknown to the english
# dictionary, so they are kept, which is the wanted behaviour.
#
# BOTH oracles are applied to the WORD, never to the emitted token. Under bigram
# the two differ, and the distinction is the whole thing: 'ho'|'ow' are not the
# word 'how', so an oracle applied after bigramming would match nothing, drop
# nothing, and be a silent no-op - every stopword would sail back into the query
# and the abstain property below would quietly stop holding. That is why the
# tokens travel with a parallel array of the word each one came from and the
# WHERE clause reads `w`, not `tok`.
#
# An all-stopword query ("how does it?") therefore filters down to nothing and
# ABSTAINS - string_agg over zero rows is NULL, to_tsquery(NULL) is NULL and
# `content_tsv @@ NULL` is NULL, so no rows, no error, and the dense half answers
# alone. This holds identically under bigram, because the filter runs on the
# words: an all-stopword query never reaches the bigramming step. Deliberate: falling
# back to the unfiltered lexemes was measured to retrieve 4 pure-noise chunks
# with noise at rank 1, and at rrf_k=60 a sparse rank 1 (1/61) outscores every
# dense hit from rank 6 down (1/66), so the fallback displaced a real answer.
# It also cost a 482-heap-block scan at 20k rows, 9.9ms against 0.05ms.
#
# Korean function words get the same treatment from KOREAN_STOPWORDS, because
# the english dictionary does not know them: without it '그것은 어떻게 하는
# 것입니까?' matched only chunks made of '그/것은/어떻게/하는' and put them at
# rank 1, and natural Korean questions were outranked by that noise. With it
# they rank the right chunk first and the josa-mismatch probes abstain.
#
# Two entries were REMOVED after measurement. Neither may come back without a
# new measurement, because both are content words in a real query:
#   '방법은' is 방법 + the topic marker - a noun, not a function word. Listed,
#     it deleted the only discriminating term in '안전사용 방법은'.
#   '이' is the demonstrative AND 이 = louse, a real pest on an agriculture
#     platform. Listed, '이 방제 약제' filtered down to lexemes the target
#     document does not carry and returned [] - the sparse half contributed
#     nothing at all. Unlisted, the demonstrative can put a function-word chunk
#     into the sparse ranking, which is the softer failure of the two: the
#     query's other lexemes and the entire dense half still compete, whereas
#     [] is total. Pinned by
#     test_a_content_word_that_looks_like_a_function_word_is_still_searchable.
#
# quote_literal is what keeps user text out of tsquery syntax: '|', '&', '!',
# ':*', '(' and a bare quote all arrive as ordinary characters inside a quoted
# lexeme. The tokens themselves come from app/retrieval/tokenize.py, whose word
# regex is [0-9a-zA-Z가-힣]+ and so cannot emit an operator in the first place,
# and they reach SQL as a bound array - as do the words and the stopword list -
# so nothing at all is concatenated into this SQL. The stopword list used to be
# f-string interpolated three lines below this claim; that was safe only because
# every entry happened to be quote-free Hangul, and `= ANY(:ko_stopwords)` is the
# same length and survives someone making the list configurable.
#
# ponytail: hand-listed Korean stopwords, because Postgres ships no Korean
# dictionary and a .stop file would need container filesystem plumbing. It only
# covers free-standing function words - josa are glued to the noun and cannot be
# listed.
#
# Under 'simple' that cost BOTH ways: '역병이' did not match a document that
# wrote '역병은' (two unrelated tokens to a whitespace tokenizer), and unlisted
# josa-glued forms like '그것은', '것이고', '것입니다', '것인가' still arrived at
# sparse rank 1 as pure function-word noise. The bigram tokenizer is what fixed
# the first half - '역병이' and '역병은' share the bigram '역병' - and that is
# most of the 0.673 -> 0.904 in tokenize.py's table. The second half is only
# softened: a josa-glued function word still contributes its bigrams, they are
# just now diluted across many more lexemes. The dense half covers the recall
# side; only the fusion covers the precision side.
#
# So this list is now a precision tool, not a recall one. Grow it only on
# measured noise, and only with words that are function words in EVERY reading;
# if it passes ~40 entries, take the morpheme analyzer in tokenize.py's ponytail
# note instead - it removes josa properly rather than out-voting them. Only 5 of
# these 15 entries are observed by a test today.
KOREAN_STOPWORDS = (
    "그",
    "저",
    "것",
    "것을",
    "것은",
    "것이",
    "이것",
    "그것",
    "무엇",
    "무엇을",
    "무엇입니까",
    "어떻게",
    "하는",
    "및",
    "또는",
)

# :tokens and :words are parallel arrays - tokens[i] was produced by words[i].
# Under 'simple' they are identical; under 'bigram' one word contributes several
# tokens, all carrying that word so the stopword oracles above can still see it.
# DISTINCT because a bigram recurs across words and to_tsvector already deduped
# what the column holds, so repeating a lexeme in the OR only makes the tsquery
# bigger; it also makes the rendered query deterministic.
#
# The ARRAY(Text) on both bindparams below is load-bearing, not decoration: it
# is what makes SQLAlchemy render `$1::TEXT[]`, and unnest takes anyarray, so an
# untyped parameter fails with "could not determine data type of parameter $1"
# before it ever reaches the table. Writing the cast inline as `:tokens::text[]`
# is not the alternative - SQLAlchemy's text() bind regex refuses a name
# followed by ':' and would leave it as literal SQL.
_TS_QUERY = text("""to_tsquery('simple',
    (SELECT string_agg(DISTINCT quote_literal(tok), ' | ')
       FROM unnest(:tokens, :words) AS u(tok, w)
      WHERE to_tsvector('english', w) <> ''::tsvector
        AND NOT w = ANY(:ko_stopwords)))""").bindparams(
    bindparam("ko_stopwords", value=list(KOREAN_STOPWORDS), type_=ARRAY(Text)),
    bindparam("tokens", type_=ARRAY(Text)),
    bindparam("words", type_=ARRAY(Text)),
)


async def keyword_search(
    db: AsyncSession,
    query_text: str,
    limit: int,
    collection_ids: list[uuid.UUID] | None = None,
    tokenizer: str = "simple",
) -> list[str]:
    """The sparse half of hybrid retrieval: ordered chunk ids, best first.

    `tokenizer` must be the one that WROTE content_tsv, not merely the one
    configured - see the note at the top of this module. It defaults to 'simple'
    because that is what an un-backfilled column holds; `hybrid_search` passes
    `settings.sparse_tokenizer`.

    Returns ids, not rows, so it is symmetric with `VectorStore.search` and
    nothing ORM-shaped or Postgres-shaped reaches the fusion stage.
    """
    # Word first, then that word's tokens: tokenize() over a single word returns
    # exactly that word's contribution, so this is the same function the ingest
    # side ran over the whole chunk, just kept per-word so the oracles can fire.
    words: list[str] = []
    tokens: list[str] = []
    for word in tokenize(query_text, "simple"):
        for token in tokenize(word, tokenizer):
            words.append(word)
            tokens.append(token)
    ts_query = _TS_QUERY.bindparams(tokens=tokens, words=words)
    # is_comparison=True types the result as boolean; without it the expression
    # inherits TSVECTOR and only happens to render correctly in a WHERE clause.
    query = select(Chunk.id).where(Chunk.content_tsv.op("@@", is_comparison=True)(ts_query))
    if collection_ids is not None:
        # `is not None`, not truthiness: an empty list means "scoped to no
        # collection" and must return nothing, exactly as in PgVectorStore.search.
        # Reading [] as unscoped would widen a Slice 3 Super Agent query from zero
        # collections to every collection in the system.
        query = query.join(Document, Document.id == Chunk.document_id).where(
            Document.collection_id.in_(collection_ids)
        )
    # ts_rank is not indexable and never filters - the @@ predicate does that, and
    # ts_rank only orders the rows the index already returned. Tie-broken by id
    # because ts_rank scores two chunks carrying the same lexemes at the same
    # positions identically, and Postgres may return those in any order; an
    # unstable sparse ranking would make RRF's own tie-break non-reproducible.
    query = query.order_by(func.ts_rank(Chunk.content_tsv, ts_query).desc(), Chunk.id).limit(limit)

    # ponytail: a GIN index defaults to fastupdate=on, so the rows a bulk ingest
    # just wrote sit in the pending list and the planner costs the index high
    # enough to pick a Seq Scan instead - measured at 20k rows, 3.9ms vs 0.02ms,
    # until VACUUM flushed it. Autovacuum fixes it on its own and Slice 1's
    # corpora are small; if first-query latency after a large ingest ever
    # matters, VACUUM chunks at the end of the pipeline or set fastupdate=off.
    result = await db.scalars(query)
    return [str(chunk_id) for chunk_id in result]
