"""The rerank stage: a model re-orders the fused candidate set before top-N.

WHICH IMPLEMENTATION, AND WHY NOT THE OTHER ONE. Two were on the table:

  * a LOCAL CROSS-ENCODER (bge-reranker-v2-m3, which is genuinely strong on
    Korean). Free per query and the better model on this corpus - but it drags
    torch and transformers into a python:3.13-slim image and 2.3GB of weights
    into the deployment, and with no GPU on this host it scores 40 candidates on
    CPU in seconds, not milliseconds. It also has to be downloaded, pinned and
    warmed, and its first request pays for all of that.
  * an LLM LISTWISE RERANK through the `LLMProvider` this application already
    has. One completion per question, no new dependency, no image growth, and
    the cheap model is already configured and already paid for elsewhere.

The LLM one shipped, because it adds nothing to the image and because the corpus
question is settled by measurement rather than by model size - see the plan in
docs/superpowers/plans/2026-08-31-expansion-and-rerank.md for the numbers. If
the cross-encoder is ever wanted, it is a second `Reranker` subclass and
`make_reranker` picks it; nothing above this module changes. THAT is what the
ABC is for, and it is why only one of the two exists.
"""

import asyncio
import logging
import re
from abc import ABC, abstractmethod

from app.core.config import Settings
from app.core.tokens import count_tokens
from app.llm.base import ChatMessage, LLMProvider
from app.retrieval.evidence import RetrievedChunk

logger = logging.getLogger("mopan.retrieval")


class Reranker(ABC):
    """Operates on domain objects, never ORM models, and may reorder AND rescore.
    It is called on the full candidate set before top-N truncation, otherwise a
    real cross-encoder could never promote anything.

    The RETURNED ORDER IS AUTHORITATIVE: the caller truncates the list as it comes
    back and never re-sorts by `rerank_score`, so an implementation that sets
    scores without reordering is a silent no-op."""

    @abstractmethod
    async def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]: ...


class NoneReranker(Reranker):
    """Slice 1 default: keeps the RRF-fused order as-is.

    It deliberately leaves `rerank_score` at None rather than copying the RRF
    score into it. A trace that shows a reranker score is claiming a reranker
    ran; "no reranker" has to stay distinguishable from "the reranker agreed".
    """

    async def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        return candidates


# How much of each candidate the model is shown. The corpus chunks are ~1000
# characters (CHUNK_SIZE) and this is what bounds the request: at
# RETRIEVAL_CANDIDATE_LIMIT=40 the whole prompt is ~40 x 600 characters, roughly
# 22k tokens of Korean, which is the per-question cost quoted in the plan. Raising
# it raises that bill linearly.
SNIPPET_CHARS = 600

SYSTEM_PROMPT = """당신은 한국 특허·상표 심사기준 문서 검색 결과의 재순위 판정기입니다.
질문 하나와 번호가 붙은 문서 조각 목록을 받습니다.
질문에 직접 답하는 내용을 담은 조각이 앞에 오도록 번호를 다시 나열하세요.

- 모든 번호를 정확히 한 번씩, 관련도가 높은 순서대로 나열합니다.
- 쉼표로 구분한 번호만 출력합니다. 설명, 문장, 코드블록을 쓰지 마세요.
- 질문의 낱말이 그대로 들어 있다는 이유만으로 앞에 두지 마세요.
  규칙을 실제로 서술한 조각이, 그 규칙을 언급만 한 조각보다 앞입니다.
- 조각이 규칙의 예외(다만, 그러하지 아니하다 등)를 서술한다면,
  질문이 그 예외를 묻고 있을 때 앞에 둡니다."""

_NUMBER = re.compile(r"\d+")


class LLMReranker(Reranker):
    """One listwise completion per query, through the provider already wired in.

    Listwise, not pointwise: pointwise would be one completion PER CANDIDATE - 40
    calls where this makes 1 - and the whole reason this implementation was
    chosen over a cross-encoder is that it costs one cheap completion.

    EVERY failure path returns the candidates in their incoming order with
    `rerank_score` still None. That is not laziness about error handling, it is
    the ABC's own distinction: None means "no reranker verdict exists for this
    item", and a timed-out or malformed rerank has produced no verdict. A trace
    showing scores after a failed rerank would be claiming a judgement nobody
    made, and the RRF order it fell back to is exactly what shipped before.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        *,
        model: str,
        timeout: float = 20.0,
        snippet_chars: int = SNIPPET_CHARS,
    ) -> None:
        self.llm_provider = llm_provider
        self.model = model
        self.timeout = timeout
        self.snippet_chars = snippet_chars

    def _order(self, text: str, size: int) -> list[int] | None:
        """The answer as a permutation of 0..size-1, or None if it is not usable.

        Extra, repeated and out-of-range numbers are DROPPED and missing ones are
        appended in their incoming order, so a model that lists 12 of 20 still
        produces a usable ranking: it has expressed an opinion about the 12 it
        named and none about the rest, and the rest keep the RRF order they came
        in with. None is returned only when the answer named nothing at all,
        which is the case where there is no opinion to honour.
        """
        seen: set[int] = set()
        order: list[int] = []
        for match in _NUMBER.finditer(text):
            index = int(match.group()) - 1  # the prompt numbers from 1
            if 0 <= index < size and index not in seen:
                seen.add(index)
                order.append(index)
        if not order:
            return None
        order.extend(i for i in range(size) if i not in seen)
        return order

    async def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        # One candidate cannot be re-ordered and zero cannot be scored; either way
        # the completion would buy nothing.
        if len(candidates) < 2:
            return candidates

        listing = "\n\n".join(
            f"[{position}] {candidate.content[: self.snippet_chars]}"
            for position, candidate in enumerate(candidates, start=1)
        )
        try:
            result = await asyncio.wait_for(
                self.llm_provider.chat(
                    [
                        ChatMessage(role="system", content=SYSTEM_PROMPT),
                        ChatMessage(role="user", content=f"질문: {query}\n\n{listing}"),
                    ],
                    temperature=0.0,
                    model=self.model,
                    # 6 characters per index ("12, ") with headroom, so a model
                    # that starts explaining is cut off rather than billed for an
                    # essay - and a truncated list still parses, because a partial
                    # permutation is handled above.
                    max_tokens=8 * len(candidates) + 32,
                ),
                timeout=self.timeout,
            )
            order = self._order(result.content, len(candidates))
        except Exception as exc:  # noqa: BLE001 - see the class docstring
            logger.warning(
                "rerank failed; keeping the RRF order",
                extra={"extra_fields": {"error": str(exc), "candidates": len(candidates)}},
            )
            return candidates
        if order is None:
            logger.warning(
                "rerank returned no usable ordering; keeping the RRF order",
                extra={"extra_fields": {"candidates": len(candidates)}},
            )
            return candidates

        reordered = [candidates[i] for i in order]
        # Reciprocal rank, so the score is monotone with the order the caller is
        # about to truncate and 0.0 never appears - `chunk_to_evidence` treats 0.0
        # as a real verdict, and a rank has no such thing as "irrelevant".
        for position, candidate in enumerate(reordered, start=1):
            candidate.rerank_score = 1.0 / position
        logger.info(
            "rerank",
            extra={
                "extra_fields": {
                    "model": self.model,
                    "candidates": len(candidates),
                    "prompt_chars": len(listing),
                    "prompt_tokens_est": count_tokens(listing),
                }
            },
        )
        return reordered


def make_reranker(settings: Settings, llm_provider: LLMProvider) -> Reranker:
    """The ONE place that decides whether a reranker runs.

    It exists so that the four call sites in `app/chat/router.py` say the same
    thing, and so that turning reranking off is one empty `RERANK_MODEL` rather
    than four edits. Off is the default and off costs nothing: `NoneReranker` is
    the object every one of those call sites constructed before this existed.
    """
    if not settings.rerank_model:
        return NoneReranker()
    return LLMReranker(
        llm_provider,
        model=settings.rerank_model,
        timeout=settings.rerank_timeout_seconds,
    )
