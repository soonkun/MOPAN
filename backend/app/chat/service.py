import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from functools import partial

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.attachments.service import claim as claim_attachments
from app.chat.prompt import (
    MANDATORY_TOKEN_ALLOWANCE,
    PromptTemplate,
    build_prompt,
    get_prompt,
    new_nonce,
)
from app.core.config import Settings
from app.core.logging import log_event
from app.core.tokens import count_tokens
from app.llm.base import LLMProvider
from app.models.conversation import Conversation
from app.models.message import Message
from app.retrieval.evidence import Evidence
from app.retrieval.reranker import Reranker
from app.retrieval.service import hybrid_search
from app.retrieval.vector_store import VectorStore
from app.workflow.catalogue import DEFAULT_WORKFLOW, ResolvedWorkflow

logger = logging.getLogger("mopan.chat")

# No digit bound. Containment is `index not in cited` against `used`, so an index
# the model invented names nothing whatever its width; a bound would only decide
# which evidence item is unciteable.
CITATION_MARKER = re.compile(r"\[(\d+)\]")
SNIPPET_CHARS = 300


@dataclass
class ChatAnswer:
    content: str
    citations: list[dict] = field(default_factory=list)
    model: str = ""
    usage: dict = field(default_factory=dict)
    latency_ms: int = 0
    prompt_name: str = ""
    prompt_version: str = ""
    # Everything the columns cannot hold: every retrieved item with its per-stage
    # scores, and whether the token budget let it reach the prompt. See
    # build_trace and app/models/message.py:Message.trace.
    trace: dict = field(default_factory=dict)


async def retrieve(
    db: AsyncSession,
    vector_store: VectorStore,
    llm_provider: LLMProvider,
    reranker: Reranker,
    question: str,
    *,
    settings: Settings,
    collection_ids: list[uuid.UUID] | None = None,
    workflow: ResolvedWorkflow = DEFAULT_WORKFLOW,
) -> list[Evidence]:
    """The DIRECT RAG path, unchanged since Slice 1 and still the default.

    Slice 6's workflow executor produces list[Evidence] a different way (a graph
    running RAG, MCP and nested-workflow nodes) and hands it to the same answer()
    below. This function is what runs when no graph does, and what the fallback
    lands on when a graph produced nothing.

    THE WORKFLOW'S COLLECTION RESTRICTION IS APPLIED HERE, not by the caller, for
    the same reason this function owns its own commit below: a boundary a caller
    has to remember is not a boundary. The router narrows too, and that is fine -
    `scope_collections` is idempotent - but this is the line that makes "a
    workflow restricted to A cannot return evidence from B" true of the direct
    RAG path however it is reached. DEFAULT_WORKFLOW restricts nothing, so
    /api/search and every caller that names none behave exactly as before."""
    # hybrid_search embeds before its first statement, so it opens no transaction
    # across that network call - but only half the property is its to keep. The
    # caller has typically just read the conversation and its history from this
    # same session, and SQLAlchemy autobegins on the first SELECT, so without this
    # the connection sits idle-in-transaction for the whole embedding round trip
    # and the pool is exhausted at a handful of concurrent chats. Ending it here
    # rather than asking every caller to remember is what makes the constraint
    # hold end to end. commit, not rollback: at this point the session holds
    # reads, and rollback would silently discard a caller's pending write.
    # Depends on expire_on_commit=False (app.core.db.make_sessionmaker), so the
    # caller's already-loaded Conversation survives the commit unexpired.
    scoped = workflow.scope_collections(collection_ids)
    await db.commit()
    # 사례 서술 -> 용어 질의 재작성 (RETRIEVAL_RECAST, 기본 꺼짐 - 측정 후 켠다).
    # 검색에만 쓰이고 answer()는 원문을 받는다: 답은 사용자의 질문에 하는 것이지
    # 우리가 고쳐 쓴 질문에 하는 것이 아니다.
    question_for_search = question
    if settings.retrieval_recast:
        from app.retrieval.recast import recast_query

        recast = await recast_query(
            llm_provider,
            question,
            model=settings.query_expansion_model,
            timeout=settings.query_expansion_timeout_seconds,
        )
        if recast:
            question_for_search = recast
            log_event(logger, "query_recast", original_chars=len(question), recast=recast)
    search = partial(
        hybrid_search,
        db,
        vector_store,
        llm_provider,
        reranker,
        question_for_search,
        top_n=settings.retrieval_top_n,
        rrf_k=settings.rrf_k,
        candidate_limit=settings.retrieval_candidate_limit,
        sparse_weight=settings.sparse_weight,
        collection_ids=scoped,
        # Neighbour expansion is opted into HERE, at the one choke point every
        # direct-RAG caller reaches - /api/chat, /api/search and the
        # orchestrator's fallback all come through this function - rather than at
        # four call sites that would each have to remember. CHUNK_OVERLAP is not
        # a chunking detail leaking into retrieval: it is how expansion knows
        # which repeated characters to drop when it joins two chunks.
        neighbor_expansion=settings.neighbor_expansion,
        chunk_overlap=settings.chunk_overlap,
        token_budget=settings.answer_context_token_budget,
        # Multi-query expansion is opted into at the same choke point and for the
        # same reason. It is NOT passed by `app/workflow/tools.py`'s RagTool, on
        # purpose: a workflow graph has already decomposed the question into
        # several RAG nodes, so expanding each of those would be paying a
        # completion to re-derive a decomposition the planner just made.
        # `query_expansion` itself is NOT bound here - it is the one argument
        # that differs between the two passes below.
        query_expansion_model=settings.query_expansion_model,
        # A timeout, not a retry: expansion is an optimisation, so the arriving
        # answer must never wait on it longer than the search it was meant to
        # improve. hybrid_search falls back to the question as asked.
        query_expansion_timeout=settings.query_expansion_timeout_seconds,
        # Query-side only, and passed here for the third time for the same
        # reason: the tokenizer the sparse arm scores the QUESTION with has to
        # match the one the index was built with, and a call site that forgets it
        # silently degrades to `simple` against a bigram index.
        sparse_tokenizer=settings.sparse_tokenizer,
        # 흔한 질의 토큰을 선택 단계에서 버리는 가난한 IDF. 0.0 = 꺼짐.
        sparse_df_trim=settings.sparse_df_trim,
        # 0.0 by default, i.e. off, and measured that way: see the note over
        # Settings.evidence_floor_rrf_score. The knob exists because a corpus
        # whose questions are NOT all answerable - unlike this fixture - may well
        # want one, and leaving it unreachable would mean re-deriving it later.
        evidence_floor=settings.evidence_floor_rrf_score,
        # 그룹 붕괴 융합 - 같은 조문·같은 분류표 섹션이 표를 나눠 갖지 않게.
        # app/retrieval/collapse.py의 측정 기록 참조. 끄면 융합은 이전과
        # 바이트 단위로 동일하다.
        collapse=settings.retrieval_collapse,
    )

    # EXPANSION IS A RETRY, NOT A STAGE. Measured at QUERY_EXPANSION_COUNT=3 on
    # the 52-question fixture, always-on: anchor 0.846 -> 0.788, recall
    # 0.846 -> 0.827, and 0.18 s -> 10.2 s PER QUESTION. Always-on cannot ship at
    # that price, and always-off leaves the question that needs it unanswered -
    # so the choice is not between the two. A question the corpus answers in its
    # own vocabulary retrieves well on the first pass and never pays the second;
    # a question phrased outside that vocabulary is exactly the one whose first
    # pass comes back weak, and it gets the rewrite that rescues it.
    #
    # THE TRIGGER IS THE WEAK-EVIDENCE SIGNAL, the same judgement `answer()` uses
    # to decide whether to ask the user back. One signal, two consequences, in
    # this order: retry first, ask only if the retry ALSO failed. Asking a user
    # to rephrase before trying the rewrite ourselves is asking them to do the
    # work we declined to.
    #
    # At QUERY_EXPANSION_COUNT=0 the `and` short-circuits: no detector call, no
    # completion, one comparison. Independent of CLARIFY_ON_WEAK_EVIDENCE on
    # purpose - the retry is a retrieval improvement whether or not the operator
    # wants the clarification branch.
    evidence = await search(query_expansion=0)
    if settings.query_expansion_count and evidence_is_weak(
        evidence, min_rrf_score=settings.weak_evidence_rrf_score
    ):
        # For the same reason the caller had to commit before the first pass:
        # `hybrid_search` has just loaded chunks, so this session holds an open
        # read transaction, and the retry embeds before its first statement.
        # Without this the connection sits idle-in-transaction across that round
        # trip - the hazard the docstring above spends a paragraph on.
        await db.commit()
        retried = await search(
            query_expansion=settings.query_expansion_count,
            # 재시도에만 다른(대개 추론) 모델을 허용한다 - 근거는 config.py의
            # query_expansion_retry_model 주석의 실측.
            query_expansion_model=(
                settings.query_expansion_retry_model or settings.query_expansion_model
            ),
        )
        log_event(
            logger,
            "retrieval_retried",
            expansion=settings.query_expansion_count,
            before=len(evidence),
            after=len(retried),
            # Did the second pass actually rescue it, or is this question about to
            # reach the clarify branch anyway? The only number that says whether
            # the retry earns its latency in production.
            rescued=not evidence_is_weak(
                retried, min_rrf_score=settings.weak_evidence_rrf_score
            ),
        )
        evidence = retried
    return evidence


# The name of the prompt the weak-evidence branch answers with. A stored prompt
# like any other (app/chat/prompt.py:CLARIFY_SYSTEM_PROMPT is its fallback text),
# so the branch shows up in the trace and on the message row as
# prompt_name="clarify_agent" - which is the only record that says a question was
# answered with a question.
CLARIFY_PROMPT_NAME = "clarify_agent"
SMALLTALK_PROMPT_NAME = "smalltalk_agent"


def evidence_is_weak(items: list[Evidence], *, min_rrf_score: float) -> bool:
    """Did retrieval come back too weak to answer from? Read off the EVIDENCE.

    Never off the question. Query length says nothing - a short well-formed
    question is fine, a long vague one is not - so the only input is what the two
    arms actually returned.

    Two signals, and deliberately only two. Every extra branch is another way to
    divert a question that had a perfectly good answer, and a detector that
    interrogates users who asked answerable questions is worse than the dead end
    it replaces.

    1. THE BEST RRF SCORE is below `min_rrf_score`, PER QUERY VARIANT. RRF scores
       are comparable across queries at a fixed rrf_k, which is what makes a
       threshold mean anything: at k=60 a chunk found by BOTH arms at rank 1
       scores 2/61, one found by a single arm at rank 1 scores 1/61 = 0.0164,
       below the 0.0170 default. The best score, not `items[0]`'s: the reranker
       is allowed to reorder this list, and taking the maximum is the reading
       that triggers least often.

       THE DIVISION BY `variants` IS NOT A TWEAK OF THE THRESHOLD - it is what
       keeps the threshold comparing like with like once the retry expands. Every
       variant adds TWO ranked lists, so with four variants a chunk that only the
       sparse arm ever returned, at rank 1 each time, scores 4/61 = 0.0656 and
       clears a 0.0170 bar four times over while nothing has corroborated it.
       Measured live on the owner's question: the first pass scored 0.0164 and
       was correctly sent to the clarification; the retry scored 0.0653 on the
       same kind of evidence, cleared this signal on inflation alone, and
       answered "제9류" citing a passage about using someone else's trademark in
       a goods name and a patent claim-drafting guide. A retry that marks its own
       homework with a ruler that grew turns a dead end into a fabrication, which
       is strictly worse than the dead end this whole branch exists to replace.
    2. RETRIEVAL CORROBORATED NOTHING - no candidate was found by the dense arm
       AND the keyword arm. With sparse_weight=1.0 and no query expansion this is
       implied by (1) and adds no new trigger; it stops being implied when N
       rewrites feed both arms, and one arm agreeing with itself N times is not
       agreement.

       THE SET IT READS IS THE FUSED CANDIDATE SET, not the delivered items -
       `metadata["candidates_corroborated"]`, which `hybrid_search` computes
       before the top_n truncation. The delivered reading asked "did a
       corroborated item survive RETRIEVAL_TOP_N", which is a question about a
       cut, not about retrieval, and it answers wrong whenever the corroborated
       chunk lands below the cut: measured on 상표등록출원서 + 류 + 지정상품,
       0 of the delivered 5 were corroborated while 4 of the top-10 candidates
       were, five runs out of five, with bestRRF 0.0489-0.0653 against a 0.0170
       threshold. Every one of those runs was diverted to the clarify prompt.

       `metadata["corroborated"]` is still ORed in so that evidence built by
       hand - a test, the eval harness's older rows - keeps its old verdict; a
       delivered corroborated item is corroboration by any reading.

    SCATTER - top hits landing in unrelated sections - was considered and
    rejected. The questions whose evidence legitimately scatters are the
    cross-reference ones (준용/crossref), which are the hardest questions the
    corpus can still answer; a scatter test diverts exactly those.
    """
    if not items:
        return True
    # Anything that did not come from the corpus search has no RRF score to judge
    # by, and its presence is not retrieval failing: a user's own attachment and
    # an MCP tool result ARE evidence. Asking someone to clarify a question they
    # attached the answer to is the worst false trigger available here.
    if any(item.source_type != "rag" for item in items):
        return False
    best = max(
        (item.metadata.get("rrf_score") or 0.0) / max(item.metadata.get("variants") or 1, 1)
        for item in items
    )
    if best < min_rrf_score:
        return True
    return not any(
        item.metadata.get("candidates_corroborated") or item.metadata.get("corroborated")
        for item in items
    )


def _citations_from(answer_text: str, used: list[Evidence]) -> list[dict]:
    """Only evidence the model actually cited becomes a citation. Listing all six
    retrieved chunks under an answer that used none of them is misleading.

    Indices resolve ONLY against `used` - the evidence build_prompt actually put
    in front of the model - and never against anything parsed out of the model's
    text or a chunk's content. Chunk content can legitimately contain a forged
    "[9] (evil.pdf, p.1)" line: folding whitespace in a document body would
    destroy real text, so the prompt layer lets it through and the containment is
    here. An index with no entry in `used` names nothing and is dropped.
    """
    cited = {int(marker) for marker in CITATION_MARKER.findall(answer_text)}
    citations: list[dict] = []
    for index, item in enumerate(used, start=1):
        if index not in cited:
            continue
        metadata = item.metadata
        citations.append(
            {
                "index": index,
                # source_type and ref are the only two fields EVERY Evidence
                # carries, so they are what identifies a citation. Without them a
                # Slice 3 MCP citation arrives with all five RAG keys None and the
                # client cannot tell it is a tool result or link back to it.
                "source_type": item.source_type,
                "ref": item.ref,
                # .get, not []: an MCP Evidence from Slice 2/3 carries none of
                # these keys and must still render as a source.
                "chunk_id": metadata.get("chunk_id"),
                "document_id": metadata.get("document_id"),
                "filename": metadata.get("filename"),
                "page": metadata.get("page"),
                "section": metadata.get("section"),
                "snippet": item.content[:SNIPPET_CHARS],
                "score": item.score,
            }
        )
    return citations


def evidence_utilization(delivered: int, cited: int) -> dict:
    """delivered=14 / cited=0 is retrieval failing, and until this function nobody
    was doing the division.

    `delivered` is what `build_prompt` REPORTED PUTTING IN FRONT OF THE MODEL -
    its second return value - not what retrieval found. The token budget can drop
    items, and the honest denominator is what was sent; charging the model for a
    chunk it never saw would turn a budget cut into a retrieval failure.

    `cited` is distinct evidence items referenced by the answer, which is exactly
    `len(_citations_from(...))`: that function walks `used` once and appends at
    most one entry per item, so a forged `[9]` naming nothing is already dropped
    and citing `[2]` three times is already one.

    None, not 0.0, when nothing was delivered. A division that never happened is
    not a utilization of zero, and averaging 0.0 into a dashboard would report
    the empty-corpus case as the worst retrieval there is.

    This is NOT anchor@N. anchor@N asks whether the answer-bearing chunk reached
    the model; this asks whether what reached it was worth sending, which is the
    only thing that catches padding the context out to `top_n`.
    """
    return {
        "delivered": delivered,
        "cited": cited,
        "utilization": cited / delivered if delivered else None,
        # THE SCREENSHOT, as a field rather than a comparison a reader has to
        # make. delivered=0 is not this case: nothing was sent, so nothing was
        # wasted.
        "nothing_cited": delivered > 0 and cited == 0,
    }


TRACE_VERSION = 1


def build_trace(
    evidence: list[Evidence],
    used: list[Evidence],
    *,
    settings: Settings,
    prompt: PromptTemplate,
) -> dict:
    """Everything that was retrieved, and whether it reached the prompt.

    THE CUT ITEMS ARE THE POINT. `citations` records only what the model cited,
    so an item that was retrieved at rank 9 and dropped by
    ANSWER_CONTEXT_TOKEN_BUDGET used to leave no record anywhere - and "why did
    it not answer from the document I uploaded" is almost always that. `used` is
    what `build_prompt` reports actually fitting, so `included` is measured, not
    inferred from the budget arithmetic a second time.

    Identity, not position: `build_prompt` currently stops at the first item that
    does not fit, so `used` is a prefix and `index < len(used)` would agree - but
    a later change to skip-and-continue would silently make that wrong, and the
    per-item scores here would then be attached to the wrong rows.

    `index` numbers ALL retrieved evidence from 1, and for the included items it
    is the same number the model saw and cited, because `build_prompt` enumerates
    the same list in the same order.

    Slice 3 adds `plan` and its steps as new keys here, and MCP items arrive in
    `evidence` with `source_type="mcp"`. Neither needs a migration - that is what
    the JSONB column is for.
    """
    used_ids = {id(item) for item in used}
    items = []
    for index, item in enumerate(evidence, start=1):
        metadata = item.metadata
        items.append(
            {
                "index": index,
                "source_type": item.source_type,
                "ref": item.ref,
                # .get throughout: an attachment or MCP item carries none of the
                # RAG keys and still has to appear in the trace.
                "chunk_id": metadata.get("chunk_id"),
                "document_id": metadata.get("document_id"),
                "filename": metadata.get("filename"),
                "page": metadata.get("page"),
                "section": metadata.get("section"),
                # The four Slice 1 kept SEPARATE rather than collapsing into one
                # score, for exactly this screen.
                "vector_rank": metadata.get("vector_rank"),
                "keyword_rank": metadata.get("keyword_rank"),
                "rrf_score": metadata.get("rrf_score"),
                "rerank_score": metadata.get("rerank_score"),
                # What neighbour expansion folded into this item's content. The
                # identity fields above still name the primary chunk - an expanded
                # item is ONE citation - so this list is the only thing on the
                # screen that says the text is wider than the chunk it cites.
                "neighbors": metadata.get("neighbors") or [],
                "score": item.score,
                "tokens": count_tokens(item.content),
                "snippet": item.content[:SNIPPET_CHARS],
                "included": id(item) in used_ids,
            }
        )
    return {
        "version": TRACE_VERSION,
        "retrieval": {
            "top_n": settings.retrieval_top_n,
            "candidate_limit": settings.retrieval_candidate_limit,
            "rrf_k": settings.rrf_k,
            "sparse_weight": settings.sparse_weight,
            "token_budget": settings.answer_context_token_budget,
            # What the system prompt cost, and the allowance it is charged
            # against - NOT against `token_budget`, which is the evidence's
            # (app/chat/prompt.py:MANDATORY_TOKEN_ALLOWANCE). Recorded because
            # this pair is the only thing that can answer "did the prompt take
            # my evidence": below the allowance the answer is always no, and
            # above it the difference is exactly what was taken.
            "prompt_tokens": count_tokens(prompt.text),
            "mandatory_allowance": MANDATORY_TOKEN_ALLOWANCE,
            "neighbor_expansion": settings.neighbor_expansion,
            "evidence_count": len(evidence),
            "included_count": len(used),
        },
        # Duplicated from the columns on purpose, and only these two: the trace
        # has to stay readable as one object when it is pulled out of the
        # database by hand, and a prompt version that was later deleted is still
        # named here.
        "prompt": {"name": prompt.name, "version": prompt.version},
        "evidence": items,
    }


async def answer(
    llm_provider: LLMProvider,
    question: str,
    history: list[dict],
    evidence: list[Evidence],
    *,
    settings: Settings,
    images: list[str] | None = None,
    model: str | None = None,
    prompt_name: str = "answer_agent",
    intent: str = "search",
    user_nickname: str | None = None,
    reasoning_effort: str | None = None,
    current_time: str | None = None,
) -> ChatAnswer:
    """Deliberately knows nothing about where `evidence` came from: no session, no
    vector store, no reranker. That is the whole point of the split - Slice 3 runs
    an execution plan over RAG and MCP steps, merges the results into one
    list[Evidence], and calls this function unchanged.

    `prompt_name` is the workflow's, and it is a defaulted keyword rather than a
    new collaborator: a workflow picks WHICH stored prompt answers, so this stays
    one `get_prompt` call and the signature that
    tests/test_chat_service.py pins - no session, no retrieval collaborator -
    is unchanged. The default is the name every caller used before workflows
    existed, which is why naming no workflow is not a second code path."""
    # THE WEAK-EVIDENCE BRANCH (spec S8). The clarification IS the answer: same
    # path, same fence, same budget, a different system prompt. It overrides the
    # workflow's `prompt_name` on purpose - a workflow picks how to answer, and
    # this is the case where there is nothing to answer from.
    #
    # Short-circuit, so CLARIFY_ON_WEAK_EVIDENCE=false costs exactly nothing: the
    # detector is not called and the clarify prompt is never loaded.
    # 의도 게이트가 "chat"으로 판정한 발화 (app/chat/intent.py). 검색이 돌지
    # 않았으므로 근거가 빈 것이 정상이고, 그 빈 근거를 약한-근거 감지기에
    # 넣으면 인사말이 되묻기로 샌다 - "안녕?"에 심사기준 인용이 달려 나가던
    # 실측 실패의 절반이 정확히 그것이었다. 잡담에는 잡담 프롬프트가 답한다.
    if intent == "chat":
        template = await get_prompt(SMALLTALK_PROMPT_NAME)
        # 호칭은 프롬프트 본문이 아니라 여기서 덧붙는다: 저장소의 프롬프트는
        # 배포 공통이고, 누구에게 말하는지는 요청마다 다르다.
        if user_nickname:
            template = PromptTemplate(
                name=template.name,
                version=template.version,
                text=(
                    f"{template.text}\n\nThe user's nickname is "
                    f"{user_nickname!r}; greet and address them by it, with 님."
                ),
            )
        clarifying = False
    else:
        clarifying = settings.clarify_on_weak_evidence and evidence_is_weak(
            evidence, min_rrf_score=settings.weak_evidence_rrf_score
        )
        template = await get_prompt(CLARIFY_PROMPT_NAME if clarifying else prompt_name)
    if current_time:
        # 닉네임과 같은 규칙으로 본문이 아니라 여기서 덧붙는다: 저장 프롬프트는
        # 배포 공통이고 "지금"은 요청마다 다르다. "올해"가 학습 시점의 연도로
        # 풀리던 실사고의 답변 쪽 절반.
        template = PromptTemplate(
            name=template.name,
            version=template.version,
            text=f"{template.text}\n\n{current_time}",
        )
    # THE RELEVANCE FLOOR, applied where it costs nothing. A live trace read
    # "14개 중 14개가 모델에게 전달되었습니다" for a trademark question against a
    # patent corpus: fourteen irrelevant chunks, all sent, and the model
    # (correctly) cited none of them. Padding the context to fill RETRIEVAL_TOP_N
    # is not just wasted tokens and money, it invites the model to manufacture a
    # connection to whatever it was handed.
    #
    # A GLOBAL score floor was implemented and measured first, and it is not what
    # ships: on the 52-question fixture every floor that removed anything also
    # removed real answers (0.0160 cost 3 questions their answer-bearing chunk,
    # 0.0170 cost 7), because every question in that fixture is answerable and so
    # the floor's benefit is invisible there while its cost is not. See the spec.
    #
    # Narrowing it to the weak branch makes the cost exactly zero on the same
    # fixture, because the weak detector fires on 0 of those 52. The few items
    # kept are not for answering - they are what the clarification is required to
    # ground its suggested questions in, so that it offers topics the corpus
    # actually contains instead of inventing them.
    if clarifying:
        evidence = evidence[: settings.clarify_evidence_items]
    messages, used_evidence = build_prompt(
        question,
        history,
        evidence,
        prompt=template,
        nonce=new_nonce(),
        token_budget=settings.answer_context_token_budget,
        images=images,
    )

    started = time.perf_counter()
    # tools=None in Slice 1; the parameter exists so Slice 2's MCP work does not
    # break the LLMProvider ABC.
    #
    # `model` rides **kwargs rather than joining the ABC signature:
    # OpenAIProvider.chat builds its request as {"model": self.answer_model, ...,
    # **kwargs}, so naming it here overrides the provider's construction-time
    # default and no other implementation has to change. Omitted entirely when
    # None - the caller wants the provider's own default, and model=None would
    # put a null on the wire. The router resolves it against the allowlist before
    # calling; this function is not the trust boundary and must not be used as one.
    # reasoning_effort도 model과 같은 이유로 kwargs를 탄다: 프로바이더가 추론
    # 계열 여부를 한 곳에서 판단하고, 비추론 모델에 온 값은 조용히 버린다.
    result = await llm_provider.chat(
        messages,
        tools=None,
        **({"model": model} if model else {}),
        **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)

    citations = _citations_from(result.content, used_evidence)
    # `evidence_used` and `citations` below have always been delivered and cited.
    # Both numbers were already here; nobody was dividing them, so a request that
    # sent 14 chunks and used none of them logged the same shape as one that used
    # all 14. These two keys are that division, and the flag on it.
    utilization = evidence_utilization(len(used_evidence), len(citations))
    log_event(
        logger,
        "answer_generated",
        model=result.model,
        evidence_used=len(used_evidence),
        citations=len(citations),
        evidence_utilization=utilization["utilization"],
        nothing_cited=utilization["nothing_cited"],
        latency_ms=latency_ms,
        prompt_name=template.name,
        prompt_version=template.version,
        # The false-trigger rate is the number this feature lives or dies on, and
        # this is the field it is counted from.
        clarified=clarifying,
        **{k: v for k, v in result.usage.items() if isinstance(v, int)},
    )
    return ChatAnswer(
        content=result.content,
        citations=citations,
        model=result.model,
        usage=result.usage,
        latency_ms=latency_ms,
        prompt_name=template.name,
        prompt_version=template.version,
        trace=build_trace(evidence, used_evidence, settings=settings, prompt=template),
    )


async def load_history(db: AsyncSession, conversation: Conversation, limit: int = 10) -> list[dict]:
    """Takes the Conversation, not a bare id, for the same reason retrieve() owns
    its commit: the caller cannot skip the ownership check by forgetting. It does
    not make another user's transcript unreachable - db.get(Conversation, id) still
    returns one with no check - it raises the cost of skipping, because a caller
    now has to go out of its way to produce an unchecked Conversation.

    Ordered by created_at, which is clock_timestamp() - now() would give both
    messages of a turn the same value and the order would flip at random."""
    result = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    messages = list(result)[::-1]
    return [{"role": m.role, "content": m.content} for m in messages]


async def persist_turn(
    db: AsyncSession,
    conversation: Conversation,
    question: str,
    chat_answer: ChatAnswer,
    retrieval_ms: int,
    attachment_ids: list[uuid.UUID] | None = None,
    workflow_name: str | None = None,
    workflow_version: int | None = None,
) -> uuid.UUID:
    """Returns the ASSISTANT message's id, which the SSE `done` frame carries so
    that the answer on screen can be rated and traced without a reload. Before
    this the client fabricated an id from a timestamp, and the 👍/👎 and 추적
    controls on a just-streamed answer had nothing real to point at."""
    # No flush between the two adds. SQLAlchemy does emit them as ONE executemany
    # INSERT, but asyncpg executes it as a Bind/Execute per parameter set and
    # clock_timestamp() is re-evaluated per execution, so the rows land ~1.7ms
    # apart (measured over 300 turns, 0 ties, min delta 1.67ms) rather than
    # sharing a timestamp the way a now() default would. That is
    # exactly the property Message.created_at was given clock_timestamp() for, and
    # test_the_two_messages_of_a_turn_never_share_a_timestamp guards it.
    user_message = Message(conversation_id=conversation.id, role="user", content=question, citations=[])
    db.add(user_message)
    assistant_message = Message(
        conversation_id=conversation.id,
            role="assistant",
        content=chat_answer.content,
        citations=chat_answer.citations,
        model=chat_answer.model,
        prompt_name=chat_answer.prompt_name,
        prompt_version=chat_answer.prompt_version,
        # NULL when no workflow answered, which is the app behaving as it always
        # did. Written beside `model` for the same reason: it survives a reload,
        # so a transcript can still say what answered it.
        workflow_name=workflow_name,
        workflow_version=workflow_version,
        usage=chat_answer.usage,
        latency_ms=chat_answer.latency_ms,
        retrieval_ms=retrieval_ms,
        trace=chat_answer.trace,
    )
    db.add(assistant_message)
    # AFTER both adds, so they still flush as one executemany and keep their
    # distinct clock_timestamp()s. Message.id's `default=uuid.uuid4` is a
    # FLUSH-time default, so before this both ids are None - which is why the
    # attachment claim below would otherwise quietly write message_id = NULL,
    # match its own `IS NULL` predicate, and report a rowcount that looks like
    # success. It is now unconditional because the assistant id is returned to
    # the caller whether or not the turn carried a file.
    await db.flush()
    # In the SAME transaction as the two messages: an attachment is either part of
    # a persisted turn or still unclaimed, never pointing at a message that was
    # rolled back.
    if attachment_ids:
        await claim_attachments(db, attachment_ids, conversation.user_id, user_message.id)
    # Without this the sidebar is frozen in creation order: `onupdate` only fires
    # when some column on the conversation row itself changes.
    await db.execute(
        update(Conversation).where(Conversation.id == conversation.id).values(updated_at=func.now())
    )
    await db.commit()
    # Readable after the commit because make_sessionmaker sets
    # expire_on_commit=False; without that this would emit a SELECT on a closed
    # session.
    return assistant_message.id
