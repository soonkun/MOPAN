from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import Request
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[3] == repo root
REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_DB_PASSWORDS = ("mopan", "postgres", "password")

# Per-input token ceiling for OpenAI's text-embedding-3-* models.
EMBEDDING_INPUT_TOKEN_LIMIT = 8191
# Element ceiling for one embeddings request's input array.
EMBEDDING_MAX_BATCH_SIZE = 2048

# Hard ceiling on the EXTRA queries multi-query expansion may generate, whatever
# QUERY_EXPANSION_COUNT asks for. Each one is a dense search plus a sparse search
# plus two more RRF rankings, and past a handful the added rankings are
# near-duplicates of each other while the latency is not. Here rather than in
# app/retrieval/expansion.py for the same reason as the line above: the value
# bounds a setting, so the validator that rejects the setting has to see it
# without importing retrieval.
MAX_EXTRA_QUERIES = 5

# There is no capability query on the chat endpoint - a model that cannot see
# images answers an image part with an opaque 400 - so vision support has to be
# asserted, not discovered. Deliberately a short, conservative PREFIX allowlist:
# a false negative refuses an image upload with a Korean message naming the model,
# which an operator fixes with one env var (ANSWER_MODEL_SUPPORTS_VISION=true),
# while a false positive is the raw provider error this exists to prevent. Note
# what is NOT here: the o1/o3/o4 reasoning families, whose -mini members are
# text-only, so the whole family is left to the override.
VISION_CAPABLE_MODEL_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-4-vision", "gpt-5", "chatgpt-4o")

# Display names for the ids an operator is likely to allow. Falling back to the
# id is not a degraded case - a label is a nicety for the picker, never a gate,
# so a model nobody thought to list here is still selectable under its own name.
MODEL_LABELS = {
    "gpt-4o": "GPT-4o",
    "gpt-4o-mini": "GPT-4o mini",
    "gpt-4.1": "GPT-4.1",
    "gpt-4.1-mini": "GPT-4.1 mini",
    "gpt-5": "GPT-5",
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "gpt-5.6-luna": "GPT-5.6 Luna",
    "gpt-5.6-sol": "GPT-5.6 Sol",
}

# reasoning_effort를 받는 계열. VISION과 같은 이유로 조회가 아니라 단언이다 -
# 다만 방향이 반대다: 여기 없는 모델에 effort를 보내면 원문 400이 나가므로,
# 거짓 양성이 사고이고 거짓 음성은 조절 못 하는 불편에 그친다. gpt-5 계열의
# -chat- 변형(gpt-5-chat-latest 등)은 비추론 대화 모델이라 제외한다.
REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def model_supports_reasoning(model: str) -> bool:
    return model.startswith(REASONING_MODEL_PREFIXES) and "chat" not in model


class Settings(BaseSettings):
    # env_file is anchored to the repo root. Resolving it against the process CWD
    # means every documented command (run from backend/) silently loads zero
    # settings and boots on defaults.
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Literal, not str: a typo like ENVIRONMENT=Production would otherwise
    # silently disable every production safeguard that compares against
    # "production". Fail at startup instead.
    environment: Literal["development", "production"] = "development"

    # 127.0.0.1, not localhost: on Windows localhost resolves to ::1 first and
    # every connect pays a failed IPv6 attempt first (2076ms vs 31ms). See the
    # note in .env.example.
    database_url: str = "postgresql+asyncpg://mopan:mopan@127.0.0.1:5432/mopan"
    redis_url: str = "redis://127.0.0.1:6379/0"
    db_pool_size: int = 10
    db_max_overflow: int = 10

    cors_origins: list[str] = ["http://localhost:3000"]

    session_ttl_seconds: int = 86400
    allow_self_registration: bool | None = None  # None -> enabled outside production

    openai_api_key: str = ""
    answer_model: str = "gpt-4o"
    # The admin-controlled allowlist a user picks an answer model from. It is a
    # COST boundary as much as a correctness one - the operator pays per call and
    # gpt-4o is many times the price of gpt-4o-mini - so an arbitrary model string
    # from a client must never reach the provider. Read through
    # `selectable_models`, which always includes ANSWER_MODEL; leave this empty
    # and the picker offers exactly the default, which is the pre-existing
    # behaviour.
    answer_models: list[str] = []
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    embedding_batch_size: int = 128
    embedding_batch_chars: int = 200_000
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 3

    rrf_k: int = 60
    retrieval_top_n: int = 6
    # HOW DEEP EACH ARM SEARCHES before RRF fuses them. DEPLOYED AT 10 through
    # `app_settings`, and 10 is a measured choice, not the smallest safe number.
    # Swept 10 / 20 / 40 at the deployed configuration (top_n 8, retry 3), three
    # runs each:
    #
    #   52-question fixture   recall  anchor   retry   ms/q  tok/q   (deterministic)
    #     limit 10             0.923   0.904    0/52  220.5   3271
    #     limit 20             0.904   0.904    0/52  222.4   3306
    #     limit 40             0.865   0.865    0/52  222.3   3329
    #
    #   colloquial fixture    recall  anchor  clarify  bestRRF  (3 runs, spread)
    #     limit 10             0.500   0.200   6,6,6    0.0184-0.0195
    #     limit 20        0.500-0.600  0.100-0.200  4,4,4  0.0219-0.0222
    #     limit 40        0.500-0.600  0.100-0.200  3,4,3  0.0232-0.0234
    #
    # DEPTH BUYS NOTHING AND COSTS RECALL. On the corpus-vocabulary fixture it is
    # a pure loss. On the colloquial one it halves the clarification rate while
    # the anchor rate - whether the answer-bearing sentence reached the model at
    # all - does not improve, so every clarification it removes becomes an answer
    # written without the sentence that answers it.
    #
    # Measured on the owner's 상표출원 question, which is what raising this was
    # proposed for. At limit 10 the two arms agree on nothing and the question is
    # correctly diverted to the clarification. At limit 20 they agree on exactly
    # two chunks - p.367 (긴급한 처리가 필요한 상표등록출원, dense 4 / sparse 18)
    # and p.131 (요지변경, dense 10 / sparse 12) - neither of which has anything
    # to do with the question, and that is the whole of what lifts the best score
    # from 0.0164 to 0.0284 and switches both weak-evidence signals off. The
    # answer that then ships cites NOTHING, three runs out of three, and invents
    # its 상품류 list ("소프트웨어, 모바일 애플리케이션, 온라인 서비스"); the
    # correct 제9류/G390802 appears in none of them.
    #
    # p.89, the chunk raising the depth was supposed to rescue, is not what any
    # depth rescues. Its ranks over the whole corpus, per arm, measured:
    #
    #                          dense   sparse
    #   full question           91        2     <- the §36① chunk
    #   등록대상 alone          282        1     <- same chunk
    #   등록대상 alone           15       62     <- a DIFFERENT p.89 chunk
    #
    # The chunk that carries "3. 상표 4. 지정상품 및 ... 상품류" is at sparse 1-2
    # and dense 91-282, so no depth an operator can set corroborates it, and it
    # was being DELIVERED at limit 10 the whole time - the clarify branch is what
    # threw it away. Depth does not fix that; it only silences the branch using
    # chunks that have nothing to do with the question.
    retrieval_candidate_limit: int = 20

    # GROUP-FOLD FUSION: fold chunks that share an address - the same 항 of the
    # same document, or the same classification-table section - into one fused
    # candidate, so copies stop occupying several of the 10 slots. Measured on
    # the 86-question fixture: ALL anchor 0.872 -> 0.884 and crossref
    # 0.880 -> 0.920 with every other group held; the folded-free slots are
    # where the second hop of a cross-reference had been getting cut. The
    # measured table, including the three more ambitious variants this REJECTED
    # (vote pooling, overfetch, article-level folding), lives at the top of
    # app/retrieval/collapse.py. Off means fusion is byte-identical to before.
    retrieval_collapse: bool = True
    # 첫 검색 전에 사례 서술을 전문 용어 질의로 다시 쓰는 단계
    # (app/retrieval/recast.py). 약근거 재시도가 못 잡는 사각 - 근거가 강해
    # "보이는" 오답 - 을 겨눈다. 켜기 전에 eval_retrieval.py로 잰다.
    retrieval_recast: bool = False

    # HOW THE SPARSE ARM TOKENISES, at ingest AND at query time - the two must
    # agree or the index answers a question nobody asked. See
    # app/retrieval/tokenize.py for the functions and
    # docs/superpowers/specs/2026-08-31-retrieval-redesign.md for the numbers.
    #
    # Measured on the live 2578-chunk manual with the 52-question fixture,
    # top_n=14, candidate_limit=20, sparse arm ALONE / fused with the dense arm:
    #
    #                       sparse-only  fused   tokenizer-group  ms/q  new dep
    #   simple (whitespace)       0.673  0.846             0.750    24        -
    #   bigram                    0.904  0.962             1.000    71     none
    #   morphemes (kiwipiepy)     0.923  0.942             1.000    31   30 MB
    #
    # BIGRAM, because it scores highest, needs NO new dependency, and its
    # tokenizer is six lines. Morphological analysis is the standard answer for
    # Korean search (Elasticsearch's nori) but "standard" is not "necessary
    # here", and it loses on this corpus while costing a C++ extension.
    #
    # 'simple' is kept as a switch, not as a fallback: it is what the query side
    # must use to read an index that has not been backfilled yet. Changing this
    # value REQUIRES re-running scripts/backfill_tsv.py - the stored tsvector is
    # built by whichever tokenizer was configured when the chunk was written.
    #
    # ponytail: bigram tsvectors run ~380 lexemes per chunk, so the sparse arm
    # costs 71 ms at 2578 chunks against 24 ms for 'simple'. That is noise beside
    # the answer completion today and a problem at 10x the corpus. The upgrade
    # path is the morpheme row above - already measured, 0.942 fused at 31 ms -
    # and it needs only this value plus a backfill.
    sparse_tokenizer: Literal["simple", "bigram"] = "bigram"

    # RARE-TOKEN SELECTION for the sparse arm: drop query lexemes whose document
    # frequency exceeds this FRACTION of the corpus before the tsquery is built.
    # ts_rank has no IDF (keyword_search.py) and no BM25 extension can be
    # created on this deployment (redesign doc §1.4), so rarity has to act at
    # CANDIDATE SELECTION instead of scoring. Measured failure this exists for:
    # a colloquial query's common bigrams (상표/등록/이름) drowned a chunk that
    # actually carried the rare ones (마트) - sparse rank outside 300.
    # 0.0 is off and off costs nothing. Turning it on REQUIRES
    # scripts/build_lexeme_df.py to have built the sparse_lexeme_df table;
    # with the table missing the sparse arm fails loudly rather than guessing.
    sparse_df_trim: float = 0.0

    # MULTI-QUERY EXPANSION: how many EXTRA retrieval queries an LLM writes from
    # the question. 0 is off and off costs nothing - not one comparison more.
    # Every variant feeds BOTH arms, so N variants produce 2N ranked lists. See
    # app/retrieval/expansion.py.
    #
    # IT IS A RETRY, NOT A STAGE (app/chat/service.py:retrieve). The first pass
    # never expands; the rewrite is paid for only when that pass comes back weak
    # by the same signal that decides whether to ask the user to rephrase. That
    # is what makes a non-zero value affordable at all - always-on cost the
    # 52-question fixture anchor 0.846 -> 0.788 and 0.18 s -> 10.2 s per
    # question, while conditional leaves both untouched because the signal fires
    # on 0 of those 52.
    #
    # RUNTIME-SAFE, and DEPLOYED THROUGH `app_settings` (3), the same way
    # RETRIEVAL_TOP_N=5 and RETRIEVAL_CANDIDATE_LIMIT=10 are. The default here
    # stays 0 so that a caller which says nothing - every test fake included -
    # gets the two ranked lists it always got, and so that a fresh install
    # retrieves before it spends.
    query_expansion_count: int = 0
    # Env-only, not on the settings screen: it is a model name, not a number, and
    # it must stay a CHEAP model - expansion runs in front of every question and
    # is worth a fraction of a cent, not a frontier completion.
    query_expansion_model: str = "gpt-4o-mini"
    # 약근거 재시도의 확장에만 쓰는 모델. 비어 있으면 query_expansion_model.
    # 분리된 이유(실측): "학회 발표 후 출원" 사례 질문에서 mini의 재작성 3개는
    # 전부 일반어("법적 문제")에 머물렀고, gpt-5.6-luna는 3개 전부 코퍼스의
    # 용어(신규성 상실·공지예외적용·신청기간)를 만들었다. query_expansion_model
    # 자체를 추론 모델로 올리면 의도 게이트·자동 도구 숙고까지 그 지연을 내므로,
    # 이미 느린 길(약근거 재시도)에만 비싼 모델을 쓴다.
    query_expansion_retry_model: str = ""
    # A rewrite that has not answered by then is worth less than the original
    # question, which is what `expand_query` degrades to. Well under
    # LLM_TIMEOUT_SECONDS on purpose.
    query_expansion_timeout_seconds: float = 8.0

    # RERANK: the model name, and "" means NO RERANKER AT ALL - `make_reranker`
    # returns None and the stage is not in the call path. It is not a null object
    # that occupies the slot; there is no such class here any more, and there
    # must not be one again. See app/retrieval/reranker.py.
    rerank_model: str = ""
    # Well under LLM_TIMEOUT_SECONDS on purpose: a rerank that has not answered by
    # then is worth less than the RRF order it would replace, and `LLMReranker`
    # treats the timeout as its degradation path rather than as an error.
    rerank_timeout_seconds: float = 20.0

    # THE RELEVANCE FLOOR: the fused RRF score below which a candidate is not
    # delivered at all. 0.0 is off, and off costs one comparison.
    #
    # It exists because there was no floor: a live trace read "14개 중 14개가
    # 모델에게 전달되었습니다" for a trademark question against a patent corpus -
    # fourteen chunks about 외국어출원, 신규성 and 분할출원, all irrelevant, all
    # sent. Padding the context to fill RETRIEVAL_TOP_N does not merely waste
    # tokens and the owner's money, it invites the model to manufacture a
    # connection to whatever it was handed.
    #
    # A threshold is meaningful here because RRF scores are comparable across
    # queries at a fixed rrf_k. At k=60, candidate_limit=20: an item BOTH arms
    # rank first scores 2/61 = 0.0328; one arm at rank 1 scores 1/61 = 0.0164;
    # one arm at rank 20 scores 1/80 = 0.0125.
    #
    # The value is set by measurement, not by that arithmetic - see the sweep in
    # docs/superpowers/specs/2026-08-31-retrieval-redesign.md. The criterion is
    # that ~0 questions lose an answer-bearing chunk to the floor; a floor that
    # buys precision by dropping real answers is worse than the padding.
    evidence_floor_rrf_score: float = 0.0

    # Answer the user something useful when retrieval comes back weak, instead of
    # the dead end "관련 문서가 없습니다". The clarification IS the answer: it goes
    # out as ordinary assistant text through the existing chat path, with 2-4
    # concrete follow-up questions GROUNDED IN THE RETRIEVED MATERIAL. See
    # app/chat/prompt.py.
    #
    # Detected from the evidence, never from query length: a short well-formed
    # question is fine and a long vague one is not.
    #
    # ON, and the number that turned it on: the false-trigger count on the
    # 52-question fixture - every question there well-formed and answerable, so
    # every trigger is a user interrogated instead of answered.
    #
    #   dense 3-large + sparse 'simple'   3 of 52
    #   DEPLOYED: dense + sparse bigram   0 of 52
    #
    # It went to zero because the detector's real signal is ARM AGREEMENT, and
    # fixing the sparse arm is what made the arms agree. Before, 5 of 14 slots
    # carried both ranks; now 10 of 14 do. This is why it shipped off in the
    # first deploy and on in the second: the number moved because an upstream
    # stage changed, which is exactly the pattern this project has been burned by
    # twice when a constant was fitted before the bug under it was fixed.
    #
    # A single-arm pipeline makes this detector fire on everything (52 of 52,
    # measured) - nothing can be corroborated when there is one ranking. So
    # SPARSE_WEIGHT=0 and this setting must not both be set.
    clarify_on_weak_evidence: bool = True

    # THE INTENT GATE (app/chat/intent.py): one cheap completion decides whether
    # a message wants a document search or is merely conversational, BEFORE any
    # retrieval runs. Measured failure it exists for: "안녕?" went through RAG
    # and came back as a citation-laden greeting about examination standards.
    # Runs only on the direct-RAG path with no plan evidence, no attachments and
    # no hand-picked tools; every classifier failure degrades to "search", so
    # the worst this gate can do is change nothing. Costs ~$0.00002 and a few
    # hundred ms per message (query_expansion_model). Measured before shipping:
    # 96/96 fixture questions classified search, 14/14 conversational utterances
    # classified chat - see app/chat/intent.py.
    intent_gate: bool = True
    # The RRF score below which the top hit counts as weak. RRF scores are
    # bounded and comparable across queries at a fixed rrf_k, which is what makes
    # a threshold meaningful at all: a chunk found by BOTH arms at rank 1 scores
    # 2/61 = 0.0328, one found by a single arm at rank 1 scores 1/61 = 0.0164,
    # and one found by a single arm at rank 10 scores 1/70 = 0.0143.
    #
    # 0.0170 sits just above "one arm, rank 1" and below "one arm, rank 2"
    # (1/62 = 0.0161)... which is deliberately NOT where it is set. At 0.0170 the
    # false-trigger rate on the 52-question fixture was measured before this
    # value was chosen; see the spec. A detector that interrogates users who
    # asked answerable questions is worse than the dead end it replaces.
    weak_evidence_rrf_score: float = 0.0170
    # How many evidence items the clarification branch keeps. NOT for answering -
    # it is what the suggested follow-up questions must be grounded in, so they
    # name topics the corpus actually contains. Small on purpose: the branch only
    # runs when retrieval already failed, and 14 chunks of failure is the trace
    # the owner was looking at when they asked why nothing was used.
    #
    # 3 -> 14 (2026-09-05 실사고): "학회 발표 후 출원" 사례 질문에서 검색은
    # 전달 14개 안에 특허법 제30조를 담아 왔는데(픽스처 s15 anchor=HIT), 되묻기
    # 분기가 상위 3개(출원서 기재사항 청크)만 보고 "이 문서는 그 문제를 다루지
    # 않습니다"라고 답했다. 정답이 4~14위에 있는 약근거가 정확히 이 분기의
    # 사정권이므로, 되묻기가 보는 폭은 답변보다 넓어야 한다.
    clarify_evidence_items: int = 14
    # The sparse ranking's weight in RRF. Textbook RRF is 1.0 - every retriever a
    # peer - and that is the value this was measured against, on the real 854-page
    # Korean examination manual with the 20-question set in
    # scripts/eval_questions_ko.json:
    #
    #   dense only                    recall@6 0.950   relevant slots/6  2.25
    #   dense + sparse, weight 1.0    recall@6 0.900   relevant slots/6  2.10
    #   dense + sparse, weight 0.5    recall@6 0.950   relevant slots/6  2.30
    #
    # At 1.0 the sparse half is a net NEGATIVE: it loses a question the dense half
    # answers and spends 2.4 of the 6 evidence slots on chunks that are neither
    # relevant nor in the dense top 6. The arithmetic is structural, not bad luck.
    # At k=60 a sparse rank 1 scores 1/61 and a dense rank 6 scores 1/66, so ANY
    # sparse rank 1 is guaranteed a slot in the top 6 however irrelevant it is -
    # and on Korean it frequently is, because 'simple' is a whitespace tokenizer
    # and Korean is agglutinative (see keyword_search.py).
    #
    # Below ~0.92 that guarantee is gone: 0.5/61 is under the dense list's own
    # rank-20 score of 1/80, so the sparse half can promote a chunk the dense half
    # already found but can no longer seat one on its own. That is a deliberate
    # demotion from peer retriever to ranking signal, and it is why 0.5 and 0.7
    # measure identically - anything under the threshold behaves the same.
    #
    # THAT ENTIRE ANALYSIS WAS FITTED TO A BUG, and the default is back to 1.0.
    # It was measured against the corpus as pypdf had extracted it, where the
    # stored text was scrambled - digits and item markers carried out of the words
    # they belonged to. Keyword matching was therefore being done against garbage,
    # which is most of why the sparse half looked like a net negative. Re-measured
    # on the SAME 20 questions after the pdfplumber parser landed and the corpus
    # was re-ingested, the finding inverted: weight 1.0 gives recall@6 1.000 and
    # weight 0.5 gives 0.950, with dense alone at 0.950. The sparse half now earns
    # its peer status.
    #
    # The threshold arithmetic above is still true and still the reason a weight
    # below ~0.92 behaves as one setting rather than a curve. Keep it: it is what
    # to reach for if sparse ever regresses again.
    #
    # Still open, and now worth more than it was: BM25 over character bigrams
    # measured 0.400 precision at weight 1.0 against 0.358 for the shipped
    # to_tsquery, on equal recall. That is 5 slots in 120 on a 20-question set -
    # suggestive, not decisive. Grow the eval set before paying for the migration.
    # Reproduce with `python scripts/eval_retrieval.py --weights 1.0,0.5,0.0`.
    sparse_weight: float = 1.0

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

    chunking_strategy: str = "semantic"
    # Characters, for both strategies. Measured on the 1950 stored chunks of the
    # real Korean examination manual: 0.911 cl100k tokens per character (mean
    # 0.860, max 1.213 over a 400-chunk sample), so 1000 characters is ~903 tokens.
    # See .env.example for why each of the four numbers below is what it is.
    chunk_size: int = 1000
    chunk_overlap: int = 150
    # The GUARANTEE, where chunk_size is the target: 1000 chars x the 1.213
    # tokens/char worst case = 1213, rounded up for the separator residual.
    max_chunk_tokens: int = 1300
    semantic_similarity_threshold: float = 0.75
    # RETRIEVAL_TOP_N (6) x MAX_CHUNK_TOKENS (1300) = 7800, so the budget never
    # truncates a full evidence set.
    answer_context_token_budget: int = 8000

    upload_dir: Path = Path("./data/uploads")
    max_upload_size_mb: int = 50

    # 10MB, a fifth of a corpus document's 50MB, because the two files are spent
    # differently. A corpus document is chunked and only ever reaches the model a
    # few hundred tokens at a time; an attachment reaches it whole, in ONE request
    # - an image base64-encoded (+33%, so 10MB of PNG is ~13.3MB on the wire,
    # inside OpenAI's documented 20MB-per-image ceiling) and a document as text
    # competing with the RAG evidence for ANSWER_CONTEXT_TOKEN_BUDGET.
    max_attachment_size_mb: int = 10
    max_attachments_per_message: int = 5
    # None -> derived from ANSWER_MODEL via VISION_CAPABLE_MODEL_PREFIXES.
    answer_model_supports_vision: bool | None = None

    # --- MCP -----------------------------------------------------------------
    # Discovery and tool calls fetch a URL an ADMIN typed, which makes the
    # backend an SSRF proxy for everything on the internal network unless
    # something says otherwise - starting with 169.254.169.254, which hands out
    # cloud instance credentials to whoever asks. Default false; the flag exists
    # because local development registers a server on 127.0.0.1 and there is no
    # honest way around that. app/mcp/client.py:check_url is the enforcement.
    mcp_allow_private_networks: bool = False
    # Shorter than LLM_TIMEOUT_SECONDS on purpose: an MCP call sits in front of
    # the model call rather than replacing it, so its budget is additive to a
    # question the user is already waiting on.
    mcp_timeout_seconds: float = 15.0
    # A manual turn names its own tools, so this is a bound on one request, not
    # on a planner. Slice 3's orchestrator gets its own ceiling.
    max_tool_calls_per_message: int = 3

    # --- Super Agent / Orchestrator (Slice 3) --------------------------------
    # OPT-IN per question, exactly the way the answer model is picked. The direct
    # RAG path of Slice 1 stays and stays the default until the orchestrator
    # measures better on scripts/eval_questions_ko.json - a planner is a new
    # failure mode, and making it mandatory on day one means every regression is
    # two systems deep.
    #
    # IT HAS NOT MEASURED BETTER, and the default stays where it is. Measured on
    # the real 1270-chunk Korean examination manual with the 21 questions in
    # scripts/eval_questions_ko.json, at top_n=8, reproducible with
    # `python scripts/eval_retrieval.py --variants current --orchestrator`:
    #
    #   path            recall@8   anchor@8   prec@8
    #   direct             1.000      0.857    0.292
    #   orchestrator       0.905      0.714    0.226   (21/21 plans accepted,
    #                                                   3.00 steps per plan)
    #
    # The mechanism is arithmetic, not a bad planner. ANSWER_CONTEXT_TOKEN_BUDGET
    # holds roughly eight chunks of this corpus. A three-step plan therefore has
    # to spend six of those eight slots on the two supplementary queries, and on
    # a single-document corpus those queries return neighbours of what the first
    # search already found - so the plan buys duplicates with slots that were
    # carrying the answer. A plan cannot add without removing while the budget is
    # the binding constraint.
    #
    # That is a statement about THIS eval set, and it is the honest one: 21
    # single-hop questions against one manual, with no MCP server registered, is
    # the case a planner cannot help with. The case it exists for - a question
    # spanning several collections, or one that needs a tool call the corpus
    # cannot answer - has no measurement here because the fixture contains none.
    # Grow the eval set before changing this default in either direction.
    #
    # Every bound below exists because a planner that loops is a bill the
    # operator pays, and none of them is enforced by asking the model nicely:
    # the prompt states them, the executor refuses a plan that breaks them.
    orchestrator_max_steps: int = 5
    # Total TOOL steps in one plan, counted separately from the step ceiling: a
    # five-step plan of five searches costs one embedding call each, and a
    # five-step plan of five tool calls reaches five third-party servers.
    orchestrator_max_tool_calls: int = 3
    # Wall clock for the WHOLE plan, enforced with asyncio.timeout the way
    # app/worker.py bounds ingestion with PIPELINE_TIMEOUT. It sits in front of
    # the answer call rather than replacing it, so it is additive to a question
    # the user is already waiting on - hence a value under a minute.
    orchestrator_timeout_seconds: float = 45.0
    # The LOWEST risk level that must be approved by a human before it runs.
    # `destructive` by default: `write` would put a dialog in front of most
    # useful tools, and `read` in front of all of them. Ordered by
    # app.models.mcp.RISK_LEVELS, so setting this to "write" also gates
    # "destructive".
    orchestrator_approval_risk_level: str = "destructive"
    # How long a paused plan can wait for its human. A second REQUEST carrying a
    # token resumes it - not a generator held open across requests, which dies
    # with the connection - so this is the lifetime of a Redis key, and the key
    # is consumed on use so a token cannot be replayed.
    orchestrator_approval_ttl_seconds: int = 900
    # Empty means "use ANSWER_MODEL". A separate knob because planning and
    # answering are different jobs with different price/latency profiles, and
    # the Slice 1 design deferred exactly this split ("Planner/Fast/Reranker
    # 모델 역할 분리는 Slice 3 Super Agent 도입 시 함께 확장한다"). It is NOT
    # validated against `selectable_models`: that allowlist is what a CLIENT may
    # name, and this is the operator's own choice.
    planner_model: str = ""
    # Slice 6. The four ORCHESTRATOR_* bounds above now bound ONE executor,
    # whoever authored the graph - a workflow a person drew is under the same
    # wall clock and the same tool-call ceiling as a graph 슈퍼 에이전트 wrote,
    # because there is one executor. They kept their names: an operator's .env is
    # not the place the "에이전트" rename buys anything, and renaming a setting
    # silently reverts a deployment to the default.
    #
    # Two new ones, because a graph can do two things a plan could not.
    #
    # NODES, not steps. A person's graph carries `input`, `answer` and possibly a
    # `branch`, none of which is a step and none of which costs anything, so
    # ORCHESTRATOR_MAX_STEPS (5) would refuse a perfectly ordinary four-search
    # canvas. This is the ceiling on the whole picture, checked at SAVE and again
    # at RUN - a graph row can be edited in the database, and a saved graph
    # outlives the settings that were in force when it was saved.
    workflow_max_nodes: int = 20
    # HOW DEEP A WORKFLOW MAY CALL A WORKFLOW. Cycles are refused statically at
    # save, but static refusal can only see the graphs that exist at that moment:
    # a callee edited afterwards makes a cycle nobody re-checked. This is the
    # counter that catches it at run, and it is why cycle detection is double.
    # 3 rather than larger because each level multiplies the tool-call budget's
    # worst case by the nodes at that level, and nobody has asked for deeper.
    workflow_max_depth: int = 3

    @property
    def selectable_models(self) -> list[str]:
        """The allowlist as the app reads it. ANSWER_MODEL is always first and
        always present: it is what a request that names no model gets, so an
        allowlist that omitted it would refuse the default.

        A property rather than a normalisation in `_finalise` because
        `model_copy(update=...)` - which every test and `/api/search`'s top_n
        override uses - does not re-run model validators, and a list frozen at
        boot would then disagree with an overridden `answer_model`.
        """
        seen = dict.fromkeys([self.answer_model] + self.answer_models)
        return [model for model in seen if model.strip()]

    def model_supports_vision(self, model: str) -> bool:
        """Per MODEL, because the answer model is now a per-request choice: the
        old single-model derivation would send an image to whichever model the
        operator happened to make the default and blind the rest.

        ANSWER_MODEL_SUPPORTS_VISION stays an override for the DEFAULT model only
        - that is the model it was written about, and it exists for a local VLM
        whose name no prefix can recognise. Every other entry in the allowlist is
        derived from VISION_CAPABLE_MODEL_PREFIXES.
        """
        if model == self.answer_model:
            return bool(self.answer_model_supports_vision)
        return model.lower().startswith(VISION_CAPABLE_MODEL_PREFIXES)

    @property
    def any_model_supports_vision(self) -> bool:
        """What the UPLOAD gate asks. Storing an image is refused only when NO
        allowlisted model could ever look at it; whether the model the user
        actually picks can is settled at /api/chat, where the choice is known."""
        return any(self.model_supports_vision(model) for model in self.selectable_models)

    @field_validator("upload_dir")
    @classmethod
    def _absolutize_upload_dir(cls, value: Path) -> Path:
        # A relative UPLOAD_DIR resolves differently for the API (run from backend/)
        # and the worker. Anchor it so both processes agree.
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()

    @model_validator(mode="after")
    def _finalise(self) -> "Settings":
        if self.allow_self_registration is None:
            self.allow_self_registration = self.environment != "production"
        if self.answer_model_supports_vision is None:
            model = self.answer_model.lower()
            self.answer_model_supports_vision = model.startswith(VISION_CAPABLE_MODEL_PREFIXES)
        if self.environment == "production":
            if not self.openai_api_key:
                raise ValueError("OPENAI_API_KEY must be set when ENVIRONMENT=production")
            if any(f":{pw}@" in self.database_url for pw in DEFAULT_DB_PASSWORDS):
                raise ValueError("refusing to start in production with a default database password")
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must satisfy 0 <= overlap < CHUNK_SIZE")
        # The size pass treats a joining newline as one token; a rare punctuation
        # tail makes it two, so a candidate can run a few percent over. Capping at
        # half the embedding ceiling keeps that overrun harmless instead of
        # turning it into a rejected embedding call.
        if not 1 <= self.max_chunk_tokens <= EMBEDDING_INPUT_TOKEN_LIMIT // 2:
            raise ValueError(
                f"MAX_CHUNK_TOKENS must satisfy 1 <= value <= {EMBEDDING_INPUT_TOKEN_LIMIT // 2}"
            )
        # Cosine similarity is bounded to [-1, 1]. A value outside it silently
        # turns the semantic strategy into "always merge" or "never merge".
        if not -1.0 <= self.semantic_similarity_threshold <= 1.0:
            raise ValueError("SEMANTIC_SIMILARITY_THRESHOLD must satisfy -1.0 <= value <= 1.0")
        # Zero or negative degrades to one embedding request per chunk with no
        # error - just cost and latency; above 2048 the endpoint rejects the
        # array mid-document.
        if not 1 <= self.embedding_batch_size <= EMBEDDING_MAX_BATCH_SIZE:
            raise ValueError(f"EMBEDDING_BATCH_SIZE must satisfy 1 <= value <= {EMBEDDING_MAX_BATCH_SIZE}")
        if self.embedding_batch_chars < 1:
            raise ValueError("EMBEDDING_BATCH_CHARS must be at least 1")
        # reciprocal_rank_fusion rejects k < 0 (ZeroDivisionError at rank -k, and
        # negative scores that invert the ranking before it gets there). Checking
        # it here turns an operator's typo into a boot failure instead of a 500 on
        # the first query that reaches fusion.
        if self.rrf_k < 0:
            raise ValueError("RRF_K must be >= 0")
        # reciprocal_rank_fusion rejects a negative weight for the same reason it
        # rejects a negative k: a ranking that subtracts is not a ranking, and the
        # 500 would land on the first chat request rather than at boot. 0 is legal
        # and means "dense only" - a documented way to switch the sparse half off
        # without deleting it.
        if self.sparse_weight < 0:
            raise ValueError("SPARSE_WEIGHT must be >= 0")
        # Neither knob errors when it goes non-positive, it just quietly returns
        # less: RETRIEVAL_TOP_N=-1 drops the last evidence item off every answer,
        # and CANDIDATE_LIMIT=0 empties the candidate set before the reranker is
        # ever asked to score it. Boot failure beats a silently smaller corpus.
        if self.retrieval_top_n < 1:
            raise ValueError("RETRIEVAL_TOP_N must be >= 1")
        if self.retrieval_candidate_limit < 1:
            raise ValueError("RETRIEVAL_CANDIDATE_LIMIT must be >= 1")
        # `expand_query` clamps to MAX_EXTRA_QUERIES anyway, so this is not what
        # protects the cost - it is what stops the clamp from being SILENT. An
        # operator who sets 20 and gets 5 has a bill and a latency they did not
        # ask for and no message saying why. Negative is off, which 0 already
        # says, so it is a typo rather than an intention.
        if not 0 <= self.query_expansion_count <= MAX_EXTRA_QUERIES:
            raise ValueError(f"QUERY_EXPANSION_COUNT must satisfy 0 <= value <= {MAX_EXTRA_QUERIES}")
        if self.rerank_timeout_seconds <= 0:
            raise ValueError("RERANK_TIMEOUT_SECONDS must be > 0")
        if self.query_expansion_timeout_seconds <= 0:
            raise ValueError("QUERY_EXPANSION_TIMEOUT_SECONDS must be > 0")
        # A negative threshold makes every answer weak-evidence and turns the
        # product into a machine that only ever asks questions back; one above
        # the maximum reachable RRF score does the same. At rrf_k=60 the ceiling
        # is 2/(k+1) for an item both arms rank first, so anything at or above it
        # is not a threshold, it is an always-on switch with no name.
        if not 0 <= self.weak_evidence_rrf_score < 2 / (self.rrf_k + 1):
            raise ValueError(
                f"WEAK_EVIDENCE_RRF_SCORE must satisfy 0 <= value < {2 / (self.rrf_k + 1):.6f} "
                f"at RRF_K={self.rrf_k}"
            )
        # THE SAME CEILING AT THE BOTTOM OF THE CANDIDATE WINDOW, which is the
        # only way candidate depth enters the weak-evidence threshold at all.
        #
        # The score of a given chunk does NOT scale with RETRIEVAL_CANDIDATE_LIMIT
        # the way it scales with the variant count - a chunk both arms rank first
        # scores 2/(k+1) at every depth, measured 0.0320 on the 52-question
        # fixture at limit 10, 20 and 40 alike, identical to four decimals. What
        # depth changes is which chunks are VISIBLE: a chunk the other arm ranked
        # 18th contributes nothing at limit 10 and 1/(k+18) at limit 20. So there
        # is no factor to divide out, and dividing by any increasing function of
        # the depth would push the strongest evidence the pipeline can produce -
        # the one whose score never moved - under the bar.
        #
        # What IS depth-dependent is the weakest corroboration the window can
        # hold: both arms at the very bottom score 2/(k+limit). Above that the
        # threshold rejects a chunk BOTH arms returned, which makes the score arm
        # contradict the agreement arm beside it rather than complement it, and
        # the pair stops being separable entirely at limit >= k+2 where
        # 2/(k+limit) falls to the 1/(k+1) of a single arm at rank 1.
        #
        # RETRIEVAL_CANDIDATE_LIMIT is runtime-settable from the settings screen
        # (max 200), so this is reachable without a deploy: at 200 and RRF_K=60
        # the floor is 0.0077 against a 0.0170 threshold. Rejecting the pair on
        # the write path beats a detector that silently stops detecting.
        corroboration_floor = 2 / (self.rrf_k + self.retrieval_candidate_limit)
        if self.weak_evidence_rrf_score >= corroboration_floor:
            raise ValueError(
                f"WEAK_EVIDENCE_RRF_SCORE must stay below {corroboration_floor:.6f} at "
                f"RRF_K={self.rrf_k} and RETRIEVAL_CANDIDATE_LIMIT="
                f"{self.retrieval_candidate_limit}, or the threshold rejects evidence both "
                f"arms returned"
            )
        # Same shape: a negative budget boots fine and then degrades into one
        # below-the-floor log per request forever, never an error.
        if self.answer_context_token_budget < 1:
            raise ValueError("ANSWER_CONTEXT_TOKEN_BUDGET must be >= 1")
        # Same shape as the retrieval knobs: neither errors when it goes
        # non-positive, it just makes every attachment upload or every attached
        # message impossible with a message that blames the user's file.
        if self.max_attachment_size_mb < 1:
            raise ValueError("MAX_ATTACHMENT_SIZE_MB must be >= 1")
        if self.max_attachments_per_message < 1:
            raise ValueError("MAX_ATTACHMENTS_PER_MESSAGE must be >= 1")
        # Same shape again: zero or negative does not error, it just makes every
        # manual tool call impossible with a message that blames the user's
        # request, and a non-positive timeout makes httpx raise on connect.
        if self.max_tool_calls_per_message < 1:
            raise ValueError("MAX_TOOL_CALLS_PER_MESSAGE must be >= 1")
        if self.mcp_timeout_seconds <= 0:
            raise ValueError("MCP_TIMEOUT_SECONDS must be > 0")
        # Same shape as every ceiling above: none of these errors when it goes
        # non-positive, it just quietly makes the feature impossible. A plan with
        # a zero step ceiling is refused on every question and the user is told
        # their planner produced nothing; a zero budget times out before the
        # first step starts.
        if self.orchestrator_max_steps < 1:
            raise ValueError("ORCHESTRATOR_MAX_STEPS must be >= 1")
        if self.orchestrator_max_tool_calls < 0:
            raise ValueError("ORCHESTRATOR_MAX_TOOL_CALLS must be >= 0")
        if self.orchestrator_timeout_seconds <= 0:
            raise ValueError("ORCHESTRATOR_TIMEOUT_SECONDS must be > 0")
        if self.orchestrator_approval_ttl_seconds < 1:
            raise ValueError("ORCHESTRATOR_APPROVAL_TTL_SECONDS must be >= 1")
        # 3 is the floor, not 1: input + answer + one tool node is the smallest
        # graph that does anything, and a ceiling below it would refuse every
        # workflow at save with a message about a limit nobody set on purpose.
        if self.workflow_max_nodes < 3:
            raise ValueError("WORKFLOW_MAX_NODES must be >= 3")
        # 1 means "a workflow may not call a workflow", which is a legitimate
        # deployment choice; 0 would refuse the top-level run itself.
        if self.workflow_max_depth < 1:
            raise ValueError("WORKFLOW_MAX_DEPTH must be >= 1")
        # A typo here is the one that matters: an unrecognised level would make
        # `RISK_LEVELS.index(...)` raise on the first plan that names a tool, and
        # an operator who wrote "destructve" would get an unattended destructive
        # call rather than a boot failure. Imported inside the validator because
        # app.models imports this module.
        from app.models.mcp import RISK_LEVELS

        if self.orchestrator_approval_risk_level not in RISK_LEVELS:
            raise ValueError(
                "ORCHESTRATOR_APPROVAL_RISK_LEVEL must be one of " + ", ".join(RISK_LEVELS)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


async def get_app_settings(request: Request) -> Settings:
    """Request-path dependency. get_settings() is lru_cached, so a route that
    depends on it ignores the live Settings the lifespan put on app.state (and
    the one tests swap in there). Same rule as get_db_session/get_redis.

    On top of that it now applies the `app_settings` overrides, so a value an
    admin changes reaches the NEXT request with no restart. This is the single
    indirection every route already goes through, which is what makes "every
    setting keeps its .env value as the fallback" true without asking each
    caller to remember - an empty table returns exactly `app.state.settings`.

    The session comes from `app.core.db.current_sessionmaker`, set per request by
    RequestContextMiddleware, for the same reason `get_prompt` reads it from
    there: this must not become another parameter on `Settings`, and a
    `Depends(get_db_session)` here would put a second session on every request
    that already has one. Imported inside the function because `app.core.db`
    imports this module.
    """
    base: Settings = request.app.state.settings
    from app.core.db import current_sessionmaker
    from app.core.settings_store import effective_settings

    sessionmaker = current_sessionmaker.get()
    if sessionmaker is None:
        return base
    async with sessionmaker() as session:
        return await effective_settings(session, base)
