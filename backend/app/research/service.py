"""딥 리서치 파이프라인 - 새싹이 src/deep_research/service.py:_run_pipeline 이식.

계획(하위 질의 JSON) → 검색(하이브리드, 질의마다) → 격차 분석(보완 질의, 신규 0건이면 중단)
→ 종합(마크다운 보고서) → 본문이 실제 인용한 근거만 출처로.

원본과 다른 점(계획 문서 Decisions):
- 실행은 arq 잡이고 진행은 research_runs.steps에 적힌다(폴링). 요청 수명에 묶지 않는다.
- 검색은 MOPAN hybrid_search 그대로. 근거 상한은 문자 수가 아니라 토큰 예산.
- 문서 단위 dedup을 (문서, 섹션) 단위로 바꿨다: 이 코퍼스는 800쪽짜리 문서 몇 개라
  문서당 청크 1개면 근거가 사실상 사라진다. 같은 문서의 다른 조항은 다른 근거다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.prompt import get_prompt
from app.core.config import Settings
from app.core.logging import log_event
from app.core.tokens import count_tokens
from app.llm.base import ChatMessage, LLMProvider
from app.models.research import ResearchInstruction, ResearchProject, ResearchRun, clamp_budget
from app.research.prompts import (
    NO_EVIDENCE_REPORT,
    gap_messages,
    planner_messages,
    scope_digest,
    synthesis_messages,
)
from app.retrieval.evidence import Evidence

logger = logging.getLogger("mopan.research")

PLANNER_PROMPT = "research_planner"
GAP_PROMPT = "research_gap"
SYNTHESIS_PROMPT = "research_synthesis"
SCOPE_PROMPT = "research_scope"
# 검토 대상 요약을 뽑을 때 읽는 첨부 앞부분. 계획서의 목표·대상·방법은 앞쪽에 있다.
SCOPE_INPUT_CHARS = 16_000

MAX_GAP_QUERIES = 3
MAX_INPUT_CHARS = 30_000
SYNTHESIS_MAX_TOKENS = 8192
SYNTHESIS_TIMEOUT = 600.0
PLANNER_TIMEOUT = 90.0
SNIPPET_CHARS = 300

_CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

Retriever = Callable[[str, int], Awaitable[list[Evidence]]]


class ResearchCancelled(Exception):
    pass


def cited_numbers(report: str, total: int) -> list[int]:
    """본문이 실제로 인용한 번호만(원본 _cited_numbers). 범위 밖(지어낸 번호)은 버린다."""
    nums: set[int] = set()
    for group in _CITE_RE.findall(report or ""):
        for part in group.split(","):
            try:
                nums.add(int(part.strip()))
            except ValueError:
                continue
    return sorted(n for n in nums if 1 <= n <= total)


def clean_queries(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        q = str(item).strip() if not isinstance(item, dict) else str(item.get("query") or item.get("q") or "").strip()
        if q and len(q) >= 2 and q not in seen:
            seen.add(q)
            out.append(q[:100])
    return out


def parse_json_object(text: str) -> dict:
    cleaned = _FENCE_RE.sub("", (text or "").strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in reply")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("JSON reply is not an object")
    return value


def _key(item: Evidence) -> str:
    return str(item.metadata.get("chunk_id") or item.ref)


def merge_hits(pool: dict[str, Evidence], hits: list[Evidence]) -> int:
    added = 0
    for h in hits:
        key = _key(h)
        if key not in pool:
            pool[key] = h
            added += 1
        elif (h.score or 0) > (pool[key].score or 0):
            pool[key] = h
    return added


def rank_sources(pool: dict[str, Evidence], max_chunks: int) -> list[Evidence]:
    """(문서, 섹션)별 최고 점수 청크 1개 → 점수 내림차순 → 상한."""
    best: dict[tuple, Evidence] = {}
    for h in pool.values():
        group = (h.metadata.get("document_id"), h.metadata.get("section") or _key(h))
        cur = best.get(group)
        if cur is None or (h.score or 0) > (cur.score or 0):
            best[group] = h
    ranked = sorted(best.values(), key=lambda h: h.score or 0, reverse=True)
    return ranked[: max(1, max_chunks)]


def evidence_block(sources: list[Evidence], token_budget: int) -> tuple[str, list[Evidence], int]:
    """[n] 목록 텍스트와 실제로 담긴 근거, 잘린 개수. 읽지 않은 것은 출처가 아니다."""
    lines: list[str] = []
    used = 0
    for n, h in enumerate(sources, 1):
        page = h.metadata.get("page")
        name = h.metadata.get("filename") or h.ref
        entry = f"[{n}] {name}{f' p.{page}' if page else ''}\n{h.content.strip()}\n"
        cost = count_tokens(entry)
        if used + cost > token_budget and lines:
            break
        lines.append(entry)
        used += cost
    return "\n".join(lines), sources[: len(lines)], len(sources) - len(lines)


def source_dict(n: int, h: Evidence) -> dict:
    """채팅 Citation과 같은 모양 - 화면이 인용 렌더러를 그대로 쓴다."""
    m = h.metadata
    return {
        "index": n,
        "source_type": h.source_type,
        "ref": h.ref,
        "chunk_id": m.get("chunk_id"),
        "document_id": m.get("document_id"),
        "filename": m.get("filename"),
        "page": m.get("page"),
        "section": m.get("section"),
        "snippet": h.content[:SNIPPET_CHARS],
        "score": h.score,
    }


def build_user_input(prompt: str, attachment_text: str | None, scope: dict | None = None) -> str:
    """종합 단계의 '사용자 요청' - 요청문 + (첨부가 있으면) 검토 대상 요약 + 첨부 본문."""
    parts = [prompt.strip()]
    digest = scope_digest(scope)
    if digest:
        parts.append(f"## 검토 대상 요약 (첨부에서 추출)\n{digest}")
    if attachment_text and attachment_text.strip():
        parts.append(f"## 첨부 자료 내용\n{attachment_text.strip()}")
    return "\n\n".join(p for p in parts if p)[:MAX_INPUT_CHARS]


def planner_input(prompt: str, scope: dict | None, attachment_text: str | None) -> str:
    """플래너가 읽는 것. 첨부가 있으면 30,000자 본문이 아니라 검토 대상 요약이다 - 하위 질의는
    과제의 목표·대상·방법·산출물에서 나와야 하고, 본문 전체를 주면 앞부분(표지·목차)에 끌린다."""
    digest = scope_digest(scope)
    if digest:
        return f"{prompt.strip()}\n\n## 검토 대상 요약\n{digest}"
    if attachment_text and attachment_text.strip():
        return f"{prompt.strip()}\n\n## 첨부 자료(앞부분)\n{attachment_text.strip()[:4000]}"
    return prompt.strip()


def group_by_document(ranked: list[Evidence]) -> list[Evidence]:
    """점수순 목록을 문서별로 묶는다(문서의 첫 등장 순서 유지). 중복성 검토는 청크가 아니라
    '기존 과제 하나'를 단위로 비교해야 하므로, 같은 문서의 근거가 번호상 이웃해야 모델이
    과제별로 읽는다. 점수 상위 문서가 먼저 온다는 성질은 그대로다."""
    order: dict[str, int] = {}
    for h in ranked:
        key = str(h.metadata.get("document_id") or h.metadata.get("filename") or h.ref)
        order.setdefault(key, len(order))
    return sorted(ranked, key=lambda h: order[str(h.metadata.get("document_id") or h.metadata.get("filename") or h.ref)])


class Runner:
    """한 실행. DB 쓰기는 단계 경계마다 짧은 세션으로(진행이 곧바로 보이게)."""

    def __init__(
        self,
        run_id: uuid.UUID,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        llm_provider: LLMProvider,
        retrieve: Retriever,
    ) -> None:
        self.run_id = run_id
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.llm = llm_provider
        self.retrieve = retrieve
        self.usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}

    async def _step(self, stage: str, detail: str, *, status: str | None = None) -> None:
        async with self.sessionmaker() as db:
            run = await db.get(ResearchRun, self.run_id, with_for_update=True)
            if run is None:
                raise ResearchCancelled()
            if run.cancel_requested:
                run.status = "cancelled"
                run.finished_at = datetime.now(UTC)
                run.steps = [*run.steps, {"stage": "cancelled", "at": datetime.now(UTC).isoformat(), "detail": "사용자가 취소했습니다."}]
                await db.commit()
                raise ResearchCancelled()
            run.steps = [*run.steps, {"stage": stage, "at": datetime.now(UTC).isoformat(), "detail": detail}]
            if status:
                run.status = status
            await db.commit()

    def _account(self, result) -> None:
        for k in ("prompt_tokens", "completion_tokens"):
            self.usage[k] += int((result.usage or {}).get(k) or 0)

    async def _json(self, system: str, user: str, *, model: str, max_tokens: int, timeout: float) -> dict:
        result = await asyncio.wait_for(
            self.llm.chat(
                [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)],
                temperature=0.3,
                model=model,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            ),
            timeout=timeout,
        )
        self._account(result)
        return parse_json_object(result.content)

    async def run(self) -> None:
        async with self.sessionmaker() as db:
            run = await db.get(ResearchRun, self.run_id)
            if run is None:
                return
            project = await db.get(ResearchProject, run.project_id)
            instruction = await db.scalar(
                select(ResearchInstruction.text).where(
                    ResearchInstruction.project_id == run.project_id, ResearchInstruction.is_active.is_(True)
                )
            )
            run.status = "planning"
            run.started_at = datetime.now(UTC)
            await db.commit()
            prompt_text, attachment_text = run.prompt, run.attachment_text
            budget = clamp_budget(project.budget if project else None)
            project_name = project.name if project else ""
            planner_hint = (project.planner_hint if project else None) or ""
            model = run.model or (project.model if project else None) or self.settings.answer_model
            effort = run.reasoning_effort or (project.reasoning_effort if project else None)
            gap_model = (project.gap_model if project else None) or self.settings.query_expansion_model
        try:
            await self._pipeline(
                prompt_text, attachment_text, budget, project_name, instruction or "", model, effort, gap_model, planner_hint
            )
        except ResearchCancelled:
            logger.info("research run cancelled", extra={"extra_fields": {"run_id": str(self.run_id)}})
        except Exception as exc:
            logger.exception("research run failed")
            async with self.sessionmaker() as db:
                run = await db.get(ResearchRun, self.run_id)
                if run is not None and run.status not in ("done", "cancelled"):
                    run.status = "failed"
                    run.error = f"{type(exc).__name__}: {exc}"[:2000]
                    run.finished_at = datetime.now(UTC)
                    run.usage = self.usage
                    await db.commit()

    async def _scope(self, attachment_text: str, gap_model: str) -> dict | None:
        """0. 검토 대상 요약 - 첨부(과제 계획서)를 먼저 읽어 목표·대상·방법·산출물·핵심어를 뽑는다.
        실패하면 None(첨부 본문으로 계획한다). 결과는 행에 저장해 화면의 '검토 대상' 카드가 된다."""
        await self._step("scoping", "첨부 문서를 읽어 검토 대상을 정리하는 중", status="planning")
        scope_prompt = await get_prompt(SCOPE_PROMPT)
        try:
            raw = await self._json(
                scope_prompt.text, attachment_text[:SCOPE_INPUT_CHARS], model=gap_model, max_tokens=1200, timeout=PLANNER_TIMEOUT
            )
        except Exception as exc:
            logger.warning("research scoping failed; planning from raw attachment: %s", exc)
            await self._step("warning", "검토 대상 정리에 실패해 첨부 본문으로 계획합니다")
            return None
        scope = {k: raw.get(k) for k in ("title", "goals", "targets", "methods", "outputs", "prior_work", "keywords")}
        if not scope_digest(scope):
            return None
        async with self.sessionmaker() as db:
            run = await db.get(ResearchRun, self.run_id, with_for_update=True)
            if run is not None:
                run.scope = scope
                await db.commit()
        title = str(scope.get("title") or "").strip()
        await self._step("scoped", (f"검토 대상: {title} · " if title else "") + f"핵심어 {len(scope.get('keywords') or [])}개")
        return scope

    async def _pipeline(
        self, prompt, attachment_text, budget, project_name, instructions, model, effort, gap_model, planner_hint=""
    ) -> None:
        # 0. 검토 대상 요약(첨부가 있을 때만)
        scope = await self._scope(attachment_text, gap_model) if attachment_text and attachment_text.strip() else None
        user_input = build_user_input(prompt, attachment_text, scope)
        # 1. 계획
        await self._step("planning", "검색 계획 수립 중", status="planning")
        planner = await get_prompt(PLANNER_PROMPT)
        try:
            system, user = planner_messages(planner.text, planner_hint, planner_input(prompt, scope, attachment_text))
            raw = await self._json(system, user, model=gap_model, max_tokens=1024, timeout=PLANNER_TIMEOUT)
            sub_queries = clean_queries(raw.get("sub_queries"))[: budget["sub_queries"]]
        except Exception as exc:
            logger.warning("research plan failed; single-query fallback: %s", exc)
            sub_queries = []
        if not sub_queries:
            sub_queries = [prompt[:200].strip()]
        await self._step("planned", f"검색 관점 {len(sub_queries)}개: " + " / ".join(sub_queries))
        # 관점은 계획이 서는 순간 화면에 보여야 한다 - 끝날 때까지 기다리지 않는다.
        async with self.sessionmaker() as db:
            run = await db.get(ResearchRun, self.run_id, with_for_update=True)
            if run is not None:
                run.sub_queries = list(sub_queries)
                await db.commit()
        # 2. 검색
        pool: dict[str, Evidence] = {}
        for i, q in enumerate(sub_queries, 1):
            await self._step("searching", f"자료 검색 {i}/{len(sub_queries)}: {q}", status="searching")
            hits = await self._safe_retrieve(q, budget["top_k_per_query"])
            new = merge_hits(pool, hits)
            await self._step("searched", f"'{q}' → {len(hits)}건 (신규 {new}건, 누적 {len(pool)}건)")
        # 3. 격차 분석
        gap_queries: list[str] = []
        gap_prompt = await get_prompt(GAP_PROMPT)
        for round_no in range(1, budget["gap_rounds"] + 1):
            if not pool:
                break
            await self._step("gap", f"수집 근거 검토·보완 질의 생성 ({round_no}/{budget['gap_rounds']})", status="gap")
            digest = "\n".join(
                f"- {h.metadata.get('filename') or h.ref}: {h.content[:80]!s}".replace("\n", " ")
                for h in list(pool.values())[:20]
            )
            system, user = gap_messages(gap_prompt.text, project_name, user_input, digest)
            try:
                raw = await self._json(system, user, model=gap_model, max_tokens=512, timeout=PLANNER_TIMEOUT)
                queries = clean_queries(raw.get("sub_queries"))[:MAX_GAP_QUERIES]
            except Exception as exc:
                logger.warning("research gap analysis failed; skipping: %s", exc)
                queries = []
            if not queries:
                await self._step("gap_done", "보완 질의 없음 - 근거가 충분하다고 판단")
                break
            gap_queries.extend(queries)
            added = 0
            for i, q in enumerate(queries, 1):
                await self._step("searching", f"보완 검색 {i}/{len(queries)}: {q}", status="searching")
                hits = await self._safe_retrieve(q, budget["top_k_per_query"])
                new = merge_hits(pool, hits)
                added += new
                await self._step("searched", f"'{q}' → {len(hits)}건 (신규 {new}건, 누적 {len(pool)}건)")
            if added == 0:
                await self._step("gap_done", "보완 검색에서 새 근거 없음 - 격차 분석 종료")
                break
        all_queries = sub_queries + gap_queries
        # 4. 종합
        ranked = group_by_document(rank_sources(pool, budget["max_evidence_chunks"]))
        block, sources, dropped = evidence_block(ranked, self.settings.research_evidence_token_budget)
        if dropped:
            await self._step("budget", f"토큰 예산으로 근거 {dropped}건을 뒤에서 잘랐습니다 (읽지 않은 것은 출처에 넣지 않음)")
        if not sources:
            await self._finish(NO_EVIDENCE_REPORT, [], all_queries, model, effort, evidence_read=0)
            return
        await self._step("synthesis", f"근거 {len(sources)}건으로 보고서 작성 중 (수 분 걸릴 수 있음)", status="synthesis")
        base = await get_prompt(SYNTHESIS_PROMPT)
        system, user = synthesis_messages(base.text, instructions, user_input, block)
        kwargs: dict[str, Any] = {"model": model, "max_tokens": SYNTHESIS_MAX_TOKENS, "temperature": 0.3, "timeout": SYNTHESIS_TIMEOUT}
        if effort:
            kwargs["reasoning_effort"] = effort
        result = await asyncio.wait_for(
            self.llm.chat([ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)], **kwargs),
            timeout=SYNTHESIS_TIMEOUT + 30,
        )
        self._account(result)
        report = result.content or ""
        cited = cited_numbers(report, len(sources))
        if not cited:
            await self._step("warning", f"근거 {len(sources)}건을 줬으나 본문 인용이 0건입니다 - 출처를 비웁니다")
        await self._finish(report, [source_dict(n, sources[n - 1]) for n in cited], all_queries, result.model or model, effort, evidence_read=len(sources))

    async def _safe_retrieve(self, query: str, top_k: int) -> list[Evidence]:
        try:
            return [h for h in await self.retrieve(query, top_k) if h.source_type == "rag"]
        except Exception as exc:
            logger.warning("research retrieval failed (%r): %s", query[:50], exc)
            return []

    async def _finish(self, report: str, sources: list[dict], queries: list[str], model: str, effort, *, evidence_read: int) -> None:
        async with self.sessionmaker() as db:
            run = await db.get(ResearchRun, self.run_id, with_for_update=True)
            if run is None:
                return
            run.status = "done"
            run.report = report
            run.sources = sources
            run.sub_queries = queries
            run.model = model
            run.reasoning_effort = effort
            run.usage = {**self.usage, "evidence_read": evidence_read, "cited": len(sources)}
            run.finished_at = datetime.now(UTC)
            run.steps = [*run.steps, {"stage": "done", "at": datetime.now(UTC).isoformat(), "detail": f"보고서 {len(report)}자, 인용 {len(sources)}건"}]
            await db.commit()
        log_event(
            logger, "research_run_finished", run_id=str(self.run_id), queries=len(queries),
            evidence_read=evidence_read, cited=len(sources), report_chars=len(report), **self.usage,
        )


def make_retriever(sessionmaker, settings: Settings, llm_provider: LLMProvider, collection_ids: list[uuid.UUID] | None) -> Retriever:
    """MOPAN hybrid_search를 (query, top_k)로 감싼다. 검색마다 짧은 세션."""
    from app.retrieval.reranker import make_reranker
    from app.retrieval.service import hybrid_search
    from app.retrieval.vector_store import PgVectorStore

    async def retrieve(query: str, top_k: int) -> list[Evidence]:
        async with sessionmaker() as db:
            return await hybrid_search(
                db, PgVectorStore(db), llm_provider, make_reranker(settings, llm_provider), query,
                top_n=top_k, rrf_k=settings.rrf_k, candidate_limit=settings.retrieval_candidate_limit,
                sparse_weight=settings.sparse_weight, collection_ids=collection_ids or None,
                neighbor_expansion=settings.neighbor_expansion, chunk_overlap=settings.chunk_overlap,
                token_budget=0, sparse_tokenizer=settings.sparse_tokenizer, sparse_df_trim=settings.sparse_df_trim,
                evidence_floor=settings.evidence_floor_rrf_score, collapse=settings.retrieval_collapse,
            )

    return retrieve
