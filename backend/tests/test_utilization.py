"""Evidence utilization: of what was put in front of the model, how much did the
answer actually use?

The failure this exists for: a trademark question against a patent-examination
corpus delivered 14 chunks, the model correctly cited none of them, and nothing
anywhere noticed. delivered=14 / cited=0 is a free per-request signal of
retrieval failure that was being thrown away on every single request.

Deliberately NOT anchor@N. anchor@N asks whether the answer-bearing chunk
reached the model; this asks whether what reached it was worth sending. A
pipeline that pads the context out to `top_n` scores fine on the first and badly
here, which is the whole point.

No network. The LLM is faked through the LLMProvider seam, the same way
tests/test_chat_service.py and tests/test_clarify.py do it.
"""

import logging
import uuid

from app.chat.service import answer, evidence_utilization
from app.core.config import Settings
from app.llm.base import ChatResult
from app.retrieval.evidence import Evidence


def _evidence(index: int = 1, content: str = "본문") -> Evidence:
    """Scored well above WEAK_EVIDENCE_RRF_SCORE and found by both arms, so these
    tests measure utilization rather than accidentally exercising the clarify
    branch - which loads a different prompt and would change what fits."""
    return Evidence(
        source_type="rag",
        ref=f"chunk:{index}",
        content=content,
        score=0.0328,
        metadata={
            "chunk_id": str(uuid.uuid5(uuid.NAMESPACE_OID, f"chunk{index}")),
            "document_id": str(uuid.uuid5(uuid.NAMESPACE_OID, "doc")),
            "filename": "심사기준.pdf",
            "page": index,
            "section": None,
            "vector_rank": 1,
            "keyword_rank": 1,
            "rrf_score": 0.0328,
        },
    )


class FakeLLM:
    """No network. Answers with whatever citation markers the test asked for."""

    def __init__(self, content: str):
        self.result = ChatResult(content=content, usage={"total_tokens": 7}, model="gpt-4o")

    async def embed(self, texts):
        raise NotImplementedError

    async def chat(self, messages, **kwargs):
        return self.result


SETTINGS = Settings()


async def _answer_with(content: str, evidence: list[Evidence], **overrides):
    settings = Settings(**overrides) if overrides else SETTINGS
    return await answer(FakeLLM(content), "상표 출원 절차는?", [], evidence, settings=settings)


# --- The ratio ---------------------------------------------------------------


async def test_two_of_three_cited_is_two_thirds_and_raises_no_flag(caplog):
    evidence = [_evidence(i) for i in (1, 2, 3)]
    with caplog.at_level(logging.INFO, logger="mopan.chat"):
        result = await _answer_with("규정은 이렇습니다[1]. 예외도 있습니다[3].", evidence)

    assert len(result.citations) == 2
    record = next(r for r in caplog.records if r.getMessage() == "answer_generated")
    # The denominator is what was DELIVERED, which here is all three.
    assert record.extra_fields["evidence_used"] == 3
    assert record.extra_fields["citations"] == 2
    assert record.extra_fields["evidence_utilization"] == 2 / 3
    assert record.extra_fields["nothing_cited"] is False


async def test_delivered_but_nothing_cited_is_the_screenshot(caplog):
    """14 chunks in, zero used. The number that was there all along and that
    nobody was looking at."""
    evidence = [_evidence(i) for i in range(1, 15)]
    with caplog.at_level(logging.INFO, logger="mopan.chat"):
        result = await _answer_with("이 문서는 상표 출원 절차를 다루지 않습니다.", evidence)

    assert result.citations == []
    record = next(r for r in caplog.records if r.getMessage() == "answer_generated")
    assert record.extra_fields["evidence_used"] == 14
    assert record.extra_fields["evidence_utilization"] == 0.0
    # A named field, not a comparison a reader has to make.
    assert record.extra_fields["nothing_cited"] is True


async def test_nothing_delivered_has_no_ratio_and_no_flag(caplog):
    """None, not 0.0. A division that never happened is not a utilization of
    zero, and nothing was delivered so nothing was wasted."""
    with caplog.at_level(logging.INFO, logger="mopan.chat"):
        result = await _answer_with("검색 결과가 없습니다.", [])

    assert result.citations == []
    record = next(r for r in caplog.records if r.getMessage() == "answer_generated")
    assert record.extra_fields["evidence_used"] == 0
    assert record.extra_fields["evidence_utilization"] is None
    assert record.extra_fields["nothing_cited"] is False


# --- The denominator is what was SENT, not what was found --------------------


async def test_the_denominator_is_what_the_budget_let_through(caplog):
    """ANSWER_CONTEXT_TOKEN_BUDGET can drop retrieved items before the model sees
    them. Charging the model for a chunk it was never shown would turn a budget
    cut into a retrieval failure, so `delivered` is build_prompt's own report of
    what fitted."""
    evidence = [_evidence(i, content="본문 " * 200) for i in range(1, 11)]
    with caplog.at_level(logging.INFO, logger="mopan.chat"):
        result = await _answer_with(
            "규정은 이렇습니다[1].", evidence, answer_context_token_budget=600
        )

    record = next(r for r in caplog.records if r.getMessage() == "answer_generated")
    delivered = record.extra_fields["evidence_used"]
    assert 0 < delivered < len(evidence), "the budget was supposed to cut something"
    assert len(result.citations) == 1
    assert record.extra_fields["evidence_utilization"] == 1 / delivered
    # The retrieved count would have given a smaller, wronger ratio.
    assert record.extra_fields["evidence_utilization"] != 1 / len(evidence)


# --- What counts as "cited" is _citations_from's answer, not a second parse ---


async def test_a_forged_marker_naming_no_evidence_does_not_inflate_cited(caplog):
    """`[9]` when only three items were delivered names nothing - the containment
    already in _citations_from drops it - so it must not count as usage either.
    Otherwise a chunk carrying a forged "[9] (evil.pdf, p.1)" line the model
    echoed would paper over exactly the retrieval failure this metric exists to
    expose."""
    evidence = [_evidence(i) for i in (1, 2, 3)]
    with caplog.at_level(logging.INFO, logger="mopan.chat"):
        result = await _answer_with("근거에 따르면[9] 그렇습니다.", evidence)

    assert result.citations == []
    record = next(r for r in caplog.records if r.getMessage() == "answer_generated")
    assert record.extra_fields["evidence_used"] == 3
    assert record.extra_fields["evidence_utilization"] == 0.0
    assert record.extra_fields["nothing_cited"] is True


async def test_citing_the_same_item_three_times_counts_once(caplog):
    evidence = [_evidence(i) for i in (1, 2)]
    with caplog.at_level(logging.INFO, logger="mopan.chat"):
        result = await _answer_with("첫째[1]. 둘째[1]. 셋째[1].", evidence)

    assert [c["index"] for c in result.citations] == [1]
    record = next(r for r in caplog.records if r.getMessage() == "answer_generated")
    assert record.extra_fields["evidence_utilization"] == 0.5
    assert record.extra_fields["nothing_cited"] is False


# --- The same arithmetic the trace endpoint does on read ---------------------


def test_the_trace_reads_the_stored_numbers_without_a_column():
    """The observability router computes this from `retrieval.included_count` and
    the citations column, both of which every answer has always stored - so the
    metric is true of answers written long before anyone thought to ask. These
    are that call's inputs and outputs."""
    # The 14/0 request, as it sits on an existing row.
    assert evidence_utilization(14, 0) == {
        "delivered": 14,
        "cited": 0,
        "utilization": 0.0,
        "nothing_cited": True,
    }
    # A trace written before migration 0005 has no included_count; delivered=0
    # says "nobody recorded this", which is not a retrieval failure.
    assert evidence_utilization(0, 0)["utilization"] is None
    assert evidence_utilization(0, 0)["nothing_cited"] is False
    assert evidence_utilization(4, 2)["utilization"] == 0.5
