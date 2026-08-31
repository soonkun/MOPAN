import logging
import re
import time
import uuid
from dataclasses import dataclass, field

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
    return await hybrid_search(
        db,
        vector_store,
        llm_provider,
        reranker,
        question,
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
        # QUERY_EXPANSION_COUNT is 0 by default, so this line costs nothing until
        # someone turns it on.
        query_expansion=settings.query_expansion_count,
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
    )


# The name of the prompt the weak-evidence branch answers with. A stored prompt
# like any other (app/chat/prompt.py:CLARIFY_SYSTEM_PROMPT is its fallback text),
# so the branch shows up in the trace and on the message row as
# prompt_name="clarify_agent" - which is the only record that says a question was
# answered with a question.
CLARIFY_PROMPT_NAME = "clarify_agent"


def evidence_is_weak(items: list[Evidence], *, min_rrf_score: float) -> bool:
    """Did retrieval come back too weak to answer from? Read off the EVIDENCE.

    Never off the question. Query length says nothing - a short well-formed
    question is fine, a long vague one is not - so the only input is what the two
    arms actually returned.

    Two signals, and deliberately only two. Every extra branch is another way to
    divert a question that had a perfectly good answer, and a detector that
    interrogates users who asked answerable questions is worse than the dead end
    it replaces.

    1. THE BEST RRF SCORE is below `min_rrf_score`. RRF scores are comparable
       across queries at a fixed rrf_k, which is what makes a threshold mean
       anything: at k=60 a chunk found by BOTH arms at rank 1 scores 2/61, one
       found by a single arm at rank 1 scores 1/61 = 0.0164, below the 0.0170
       default. The best score, not `items[0]`'s: the reranker is allowed to
       reorder this list, and taking the maximum is the reading that triggers
       least often.
    2. NOTHING IS CORROBORATED - no item was found by the dense arm AND the
       keyword arm. With sparse_weight=1.0 and no query expansion this is
       implied by (1) and adds no new trigger; it stops being implied when N
       rewrites feed both arms, and one arm agreeing with itself N times is not
       agreement.

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
    if max(item.metadata.get("rrf_score") or 0.0 for item in items) < min_rrf_score:
        return True
    return not any(
        item.metadata.get("vector_rank") is not None and item.metadata.get("keyword_rank") is not None
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
    clarifying = settings.clarify_on_weak_evidence and evidence_is_weak(
        evidence, min_rrf_score=settings.weak_evidence_rrf_score
    )
    template = await get_prompt(CLARIFY_PROMPT_NAME if clarifying else prompt_name)
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
    result = await llm_provider.chat(messages, tools=None, **({"model": model} if model else {}))
    latency_ms = int((time.perf_counter() - started) * 1000)

    citations = _citations_from(result.content, used_evidence)
    log_event(
        logger,
        "answer_generated",
        model=result.model,
        evidence_used=len(used_evidence),
        citations=len(citations),
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
