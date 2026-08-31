"""Multi-query expansion: one question becomes several retrieval queries.

WHY IT IS NOT FREE, and therefore why it defaults to off. Every expansion is one
chat completion on the request path, before any search runs. So the whole module
is built around three properties, in this order:

1.  **The off path costs nothing.** `QUERY_EXPANSION_COUNT` is 0 by default and
    `hybrid_search` never calls in here at 0 - there is no "expand into a list of
    one" round trip to skip.
2.  **A failure degrades to the original query, never to an error.** Timeout,
    LLMError, a model that answers in prose, an empty answer: every one of them
    returns `[query]`, which is exactly what retrieval did before this existed.
    A retriever that breaks when the expander hiccups is worse than no expander.
3.  **The same question is paid for once per process.** Answers are cached on
    (model, count, query), bounded, and the cache is the reason a re-asked
    question and a retried request do not each bill a completion.

WHAT THE PROMPT IS FOR. `keyword_search` runs Postgres 'simple', a whitespace
tokenizer, over an agglutinative language: 상표등록출원이나 and 상표등록출원 are
two unrelated lexemes to it, and a query carrying one cannot match a chunk
carrying the other. The expander's job on this corpus is to emit the josa-free
stem, the spaced form, and the legal-term synonym as SEPARATE queries, so that
each gets its own ranking into RRF and the union covers what one surface form
misses. That is a Korean-corpus prompt, not a translated English "rephrase the
question" prompt, and it is why the examples in it are drawn from this manual.
"""

import asyncio
import logging
import re
from collections import OrderedDict

from app.core.config import MAX_EXTRA_QUERIES
from app.core.logging import log_event
from app.llm.base import ChatMessage, LLMProvider

logger = logging.getLogger("mopan.retrieval")

# Seconds. Deliberately far below LLM_TIMEOUT_SECONDS (30): expansion sits in
# front of every search on the request path, and a question answered five seconds
# late from the original query alone beats one answered thirty seconds late from
# a better one. The timeout IS the degradation path, not an error path.
EXPANSION_TIMEOUT = 5.0

# What an expansion's rankings are worth against the original question's, in RRF.
#
# NOT 1.0, and the arithmetic is the same one that demoted `sparse_weight`: at
# k=60 a rank-1 hit scores 1/61 and a rank-20 hit 1/80, so a paraphrase weighted
# as a peer can seat its own rank 1 above every result the user's actual question
# found from rank 6 down. A paraphrase is a guess about what was meant; it may
# promote a chunk the original also found, and it must not be able to seat one on
# its own. 0.5 puts an expansion's rank 1 (0.0082) below the original's rank 20
# (0.0125), which is exactly that rule.
EXPANSION_WEIGHT = 0.5

# Process-local and bounded. Not Redis: the value is worth a fraction of a cent,
# it is derived from nothing but the question, and a cross-process cache would be
# a new failure mode on the request path to save that fraction.
_CACHE_LIMIT = 512
_cache: "OrderedDict[tuple[str, int, str], list[str]]" = OrderedDict()

# Strips list furniture the model adds despite being told not to: "1. ", "- ",
# "* ", "1) ", and surrounding quotes.
_FURNITURE = re.compile(r"^\s*(?:[-*•]|\d+[.)])?\s*[\"'“‘]?|[\"'”’]?\s*$")

SYSTEM_PROMPT = """당신은 한국 특허·상표 심사기준 문서를 찾기 위한 검색어 확장기입니다.
사용자의 질문 하나를 받아, 같은 내용을 다른 표면형으로 적은 검색어 {count}개를 만듭니다.

이 검색 엔진은 공백으로 단어를 자르기 때문에 조사가 붙은 형태와 붙지 않은 형태를 서로 다른 단어로 봅니다.
따라서 다음을 반드시 지키세요.

- 조사를 뗀 형태를 따로 냅니다. 예: 상표등록출원이나 → 상표등록출원 / 상표 출원
- 붙여 쓴 법령 용어는 띄어 쓴 형태도 함께 냅니다. 예: 공지예외주장 → 공지 예외 주장
- 심사기준의 정식 용어와 실무에서 쓰는 다른 이름을 함께 냅니다.
  예: 공지예외주장 / 신규성 의제 / 공지 예외 적용, 국내우선권주장 / 조약우선권
- 문장이 아니라 명사구 검색어로 씁니다. 물음표와 서술어는 빼세요.
- 질문에 없는 새 주제를 만들지 마세요. 같은 것을 다르게 부르는 말만 씁니다.

출력 형식: 한 줄에 검색어 하나. 번호, 따옴표, 설명, 빈 줄 없이 정확히 {count}줄."""


def _parse(text: str, query: str, count: int) -> list[str]:
    """Lines out of whatever the model answered with, de-duplicated against the
    original query. Everything unusable is dropped rather than raising: a partly
    usable answer is still worth more than the original query alone."""
    seen = {query.strip()}
    out: list[str] = []
    for line in text.splitlines():
        candidate = _FURNITURE.sub("", line).strip()
        # A prose apology ("죄송합니다, ...") is a sentence, not a search term.
        # 80 characters is longer than any query in the eval fixture and shorter
        # than any refusal the model has produced.
        if not candidate or len(candidate) > 80 or candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
        if len(out) >= count:
            break
    return out


async def expand_queries(
    llm_provider: LLMProvider,
    query: str,
    *,
    count: int,
    model: str,
    timeout: float = EXPANSION_TIMEOUT,
) -> list[str]:
    """`[query, *variants]` - the ORIGINAL ALWAYS FIRST, and always present.

    First position matters beyond tidiness: `hybrid_search` weights the original
    query's two rankings at 1.0 and every expansion's below that, so the caller
    identifies the original by position and nothing has to be threaded through.

    Never raises, and NEVER RETURNS AN EMPTY LIST - `hybrid_search` reads
    `rankings[0]` and `[1]` as the original query's, so "no queries at all" is not
    a state any caller can handle. `count <= 0`, and a blank question, return
    `[query]` without touching the network.
    """
    if count <= 0 or not query.strip():
        return [query]
    count = min(count, MAX_EXTRA_QUERIES)

    key = (model, count, query)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return [query, *cached]

    try:
        result = await asyncio.wait_for(
            llm_provider.chat(
                [
                    ChatMessage(role="system", content=SYSTEM_PROMPT.format(count=count)),
                    ChatMessage(role="user", content=query),
                ],
                # 0.0, not the 0.2 default: two identical questions in one session
                # must not produce two different candidate sets, because then the
                # cache below is the only thing making retrieval reproducible and
                # a cache miss silently changes the answer.
                temperature=0.0,
                model=model,
                # Bounded output as well as bounded count: a model that ignores
                # the format and writes an essay is capped at what 5 short queries
                # cost, not at the model's context window.
                max_tokens=200,
            ),
            timeout=timeout,
        )
        variants = _parse(result.content, query, count)
    except Exception as exc:  # noqa: BLE001 - see property 2 in the module docstring
        # Broad on purpose. asyncio.TimeoutError, LLMError, and anything a future
        # provider raises all mean the same thing to retrieval: search the
        # question as the user asked it. Logged rather than swallowed silently so
        # a permanently broken expander is visible without being fatal.
        logger.warning(
            "query expansion failed; searching the original query alone",
            extra={"extra_fields": {"error": str(exc)}},
        )
        return [query]

    # A successful call that yielded nothing usable is still cached. Otherwise a
    # question the model cannot expand is paid for on every single retry.
    _cache[key] = variants
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_LIMIT:
        _cache.popitem(last=False)

    log_event(logger, "query_expansion", count=len(variants), model=model)
    return [query, *variants]
