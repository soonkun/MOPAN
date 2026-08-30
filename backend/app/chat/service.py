import logging
import re
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.attachments.service import claim as claim_attachments
from app.chat.prompt import build_prompt, get_prompt, new_nonce
from app.core.config import Settings
from app.core.logging import log_event
from app.llm.base import LLMProvider
from app.models.conversation import Conversation
from app.models.message import Message
from app.retrieval.evidence import Evidence
from app.retrieval.reranker import Reranker
from app.retrieval.service import hybrid_search
from app.retrieval.vector_store import VectorStore

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


async def retrieve(
    db: AsyncSession,
    vector_store: VectorStore,
    llm_provider: LLMProvider,
    reranker: Reranker,
    question: str,
    *,
    settings: Settings,
    collection_ids: list[uuid.UUID] | None = None,
) -> list[Evidence]:
    """Slice 3's Orchestrator will produce list[Evidence] a different way (a plan
    running RAG and MCP steps) and hand it to the same answer() below."""
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
        collection_ids=collection_ids,
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


async def answer(
    llm_provider: LLMProvider,
    question: str,
    history: list[dict],
    evidence: list[Evidence],
    *,
    settings: Settings,
    images: list[str] | None = None,
) -> ChatAnswer:
    """Deliberately knows nothing about where `evidence` came from: no session, no
    vector store, no reranker. That is the whole point of the split - Slice 3 runs
    an execution plan over RAG and MCP steps, merges the results into one
    list[Evidence], and calls this function unchanged."""
    template = await get_prompt("answer_agent")
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
    result = await llm_provider.chat(messages, tools=None)
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
) -> None:
    # No flush between the two adds. SQLAlchemy does emit them as ONE executemany
    # INSERT, but asyncpg executes it as a Bind/Execute per parameter set and
    # clock_timestamp() is re-evaluated per execution, so the rows land ~1.7ms
    # apart (measured over 300 turns, 0 ties, min delta 1.67ms) rather than
    # sharing a timestamp the way a now() default would. That is
    # exactly the property Message.created_at was given clock_timestamp() for, and
    # test_the_two_messages_of_a_turn_never_share_a_timestamp guards it.
    user_message = Message(conversation_id=conversation.id, role="user", content=question, citations=[])
    db.add(user_message)
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=chat_answer.content,
            citations=chat_answer.citations,
            model=chat_answer.model,
            prompt_name=chat_answer.prompt_name,
            prompt_version=chat_answer.prompt_version,
            usage=chat_answer.usage,
            latency_ms=chat_answer.latency_ms,
            retrieval_ms=retrieval_ms,
        )
    )
    # In the SAME transaction as the two messages: an attachment is either part of
    # a persisted turn or still unclaimed, never pointing at a message that was
    # rolled back.
    if attachment_ids:
        # AFTER both adds, so they still flush as one executemany and keep their
        # distinct clock_timestamp()s. The flush is not optional: Message.id's
        # `default=uuid.uuid4` is a *flush-time* default, so before it
        # user_message.id is None - and the claim below would then quietly write
        # message_id = NULL, match its own `IS NULL` predicate, and report a
        # rowcount that looks like success.
        await db.flush()
        await claim_attachments(db, attachment_ids, conversation.user_id, user_message.id)
    # Without this the sidebar is frozen in creation order: `onupdate` only fires
    # when some column on the conversation row itself changes.
    await db.execute(
        update(Conversation).where(Conversation.id == conversation.id).values(updated_at=func.now())
    )
    await db.commit()
