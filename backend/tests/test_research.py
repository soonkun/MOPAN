"""딥 리서치 - 원본 test_service.py의 계약 이식: 단계 순서, 읽은 근거만 출처, 계획 실패 폴백,
근거 0건이면 종합 생략, 인용 없는 보고서는 출처 비움, 토큰 예산 절단, 취소."""
import uuid

import pytest
import pytest_asyncio

from app.core.config import Settings
from app.llm.base import ChatResult
from app.models.research import ResearchInstruction, ResearchProject, ResearchRun
from app.models.user import User
from app.research.service import Runner, cited_numbers, clean_queries, evidence_block, rank_sources
from app.retrieval.evidence import Evidence


class ScriptedProvider:
    """호출 순서대로 답을 돌려준다. 계획 → (격차) → 종합."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        reply = self.replies.pop(0) if self.replies else ""
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(content=reply, model="fake", usage={"prompt_tokens": 10, "completion_tokens": 5})

    async def embed(self, texts):
        raise AssertionError("리서치는 임베딩을 직접 부르지 않는다")


def ev(chunk: str, doc: str, section: str, score: float, text: str | None = None) -> Evidence:
    return Evidence(
        source_type="rag", ref=f"chunk:{chunk}", content=text or f"{doc} {section} 본문 {chunk}", score=score,
        metadata={"chunk_id": chunk, "document_id": doc, "filename": f"{doc}.pdf", "page": 1, "section": section},
    )


def settings(**over) -> Settings:
    base = {"openai_api_key": "x", "answer_model": "gpt-4o", "answer_models": [], "research_evidence_token_budget": 60_000}
    return Settings(**{**base, **over})


@pytest_asyncio.fixture
async def project(db):
    user = User(email="r@example.com", password_hash="x", role="admin")
    db.add(user)
    await db.flush()
    p = ResearchProject(name=f"방-{uuid.uuid4().hex[:6]}", budget={"sub_queries": 3, "top_k_per_query": 5, "gap_rounds": 1, "max_evidence_chunks": 24}, created_by=user.id)
    db.add(p)
    await db.flush()
    db.add(ResearchInstruction(project_id=p.id, version=1, text="특허 실무자 관점으로 쓴다.", is_active=True, created_by=user.id))
    run = ResearchRun(project_id=p.id, prompt="학회 발표 후 특허 출원 가능성", created_by=user.id)
    db.add(run)
    await db.commit()
    return p, run, user


async def _run(test_sessionmaker, run_id, provider, retrieve, **over):
    runner = Runner(run_id, sessionmaker=test_sessionmaker, settings=settings(**over), llm_provider=provider, retrieve=retrieve)
    await runner.run()
    async with test_sessionmaker() as s:
        return await s.get(ResearchRun, run_id)


async def test_pipeline_stages_and_cited_only_sources(db, test_sessionmaker, project):
    p, run, _ = project
    hits = {
        "공지예외 요건": [ev("c1", "특허심사기준", "제30조", 0.9), ev("c2", "특허심사기준", "제29조", 0.5)],
        "12개월 기한": [ev("c1", "특허심사기준", "제30조", 0.95), ev("c3", "상표심사기준", "제34조", 0.4)],
    }

    async def retrieve(q, k):
        return hits.get(q, [])

    provider = ScriptedProvider([
        '{"sub_queries": ["공지예외 요건", "12개월 기한"]}',
        '{"sub_queries": []}',
        "## 요청 요약\n발표 후 출원 [1].\n\n## 확인된 내용\n12개월 이내 출원 [1][2].\n\n## 요약\n가능하다 [1].",
    ])
    result = await _run(test_sessionmaker, run.id, provider, retrieve)
    assert result.status == "done"
    stages = [s["stage"] for s in result.steps]
    assert stages[:2] == ["planning", "planned"] and "synthesis" in stages and stages[-1] == "done"
    assert result.sub_queries == ["공지예외 요건", "12개월 기한"]
    # 근거 3건(c1은 점수 높은 것으로 병합)을 넘겼고 본문은 [1][2]만 인용 → 출처 2건, 번호 유지.
    assert [s["index"] for s in result.sources] == [1, 2]
    assert result.sources[0]["chunk_id"] == "c1" and result.sources[0]["filename"] == "특허심사기준.pdf"
    assert result.usage["evidence_read"] == 3 and result.usage["cited"] == 2
    # 방 지침이 종합 프롬프트에 들어가고 안전 규칙이 살아 있다.
    system = provider.calls[-1][0][0].content
    assert "특허 실무자 관점" in system and "근거 사용 절대 규칙" in system and "출력 형식" in system


async def test_plan_failure_falls_back_to_single_query_and_no_evidence_skips_synthesis(db, test_sessionmaker, project):
    p, run, _ = project
    provider = ScriptedProvider([RuntimeError("planner down")])

    async def retrieve(q, k):
        return []

    result = await _run(test_sessionmaker, run.id, provider, retrieve)
    assert result.status == "done"
    assert result.sub_queries == ["학회 발표 후 특허 출원 가능성"]
    assert "근거 자료를 찾지" in result.report and result.sources == []
    assert len(provider.calls) == 1  # 종합은 부르지 않았다


async def test_uncited_report_gets_no_sources_and_gap_round_adds_queries(db, test_sessionmaker, project):
    p, run, _ = project

    async def retrieve(q, k):
        return [ev("g1", "d", "s1", 0.7)] if q == "보완" else [ev("a1", "d", "s0", 0.8)]

    provider = ScriptedProvider(['{"sub_queries": ["기본"]}', '{"sub_queries": ["보완"]}', "근거 없이 쓴 보고서."])
    result = await _run(test_sessionmaker, run.id, provider, retrieve)
    assert result.status == "done" and result.sources == []
    assert result.sub_queries == ["기본", "보완"]
    assert any(s["stage"] == "warning" for s in result.steps)


async def test_token_budget_truncates_and_records(db, test_sessionmaker, project):
    p, run, _ = project
    big = [ev(f"c{i}", "d", f"s{i}", 1 - i / 100, text="가" * 4000) for i in range(10)]

    async def retrieve(q, k):
        return big

    provider = ScriptedProvider(['{"sub_queries": ["q"]}', '{"sub_queries": []}', "보고서 [1]."])
    result = await _run(test_sessionmaker, run.id, provider, retrieve, research_evidence_token_budget=6000)
    assert result.status == "done"
    assert any(s["stage"] == "budget" for s in result.steps)
    assert result.usage["evidence_read"] < 10


async def test_cancel_request_stops_at_next_step(db, test_sessionmaker, project):
    p, run, _ = project
    run.cancel_requested = True
    await db.commit()
    provider = ScriptedProvider(['{"sub_queries": ["q"]}'])

    async def retrieve(q, k):
        return [ev("c", "d", "s", 0.5)]

    result = await _run(test_sessionmaker, run.id, provider, retrieve)
    assert result.status == "cancelled" and result.steps[-1]["stage"] == "cancelled"


def test_helpers():
    assert cited_numbers("a [1] b [2, 3] c [9]", total=3) == [1, 2, 3]
    assert clean_queries(["a", {"query": "bb"}, "a", 5]) == ["bb"]
    pool = {"x": ev("x", "d1", "s", 0.2), "y": ev("y", "d1", "s", 0.9), "z": ev("z", "d2", "t", 0.5)}
    ranked = rank_sources(pool, 10)
    assert [e.metadata["chunk_id"] for e in ranked] == ["y", "z"]  # 같은 (문서, 섹션)은 최고 점수 하나
    block, kept, dropped = evidence_block(ranked, token_budget=10)
    assert kept and dropped >= 0 and block.startswith("[1] d1.pdf")


def test_research_prompts_are_registered_with_their_own_text():
    """PLANNER_SYSTEM_PROMPT 이름 충돌 회귀 방지 - 리서치 플래너가 워크플로우 계획 프롬프트를 받았다."""
    from app.chat.prompt import _FALLBACK_PROMPTS
    from app.research.prompts import GAP_SYSTEM_PROMPT, PLANNER_SYSTEM_PROMPT, SYNTHESIS_SYSTEM_PROMPT

    assert _FALLBACK_PROMPTS["research_planner"].text == PLANNER_SYSTEM_PROMPT
    assert _FALLBACK_PROMPTS["research_gap"].text == GAP_SYSTEM_PROMPT
    assert _FALLBACK_PROMPTS["research_synthesis"].text == SYNTHESIS_SYSTEM_PROMPT
    assert "sub_queries" in PLANNER_SYSTEM_PROMPT and "steps" not in PLANNER_SYSTEM_PROMPT


# --- 재설계(2026-09-13): 검토 대상 요약 · 관점 힌트 · 문서별 묶음 · 템플릿 · PDF ---------------


async def test_an_attachment_is_scoped_first_and_the_plan_reads_the_scope(db, test_sessionmaker, project):
    """첨부가 있으면 0단계로 검토 대상 요약을 뽑아 행에 저장하고, 플래너는 30,000자 본문이
    아니라 그 요약을 읽는다. 방의 관점 힌트는 플래너 시스템 프롬프트 끝에 붙는다."""
    p, run, _ = project
    async with test_sessionmaker() as s:
        prj = await s.get(ResearchProject, p.id)
        prj.planner_hint = "관점 예시: 산출물, 검증 방법"
        r = await s.get(ResearchRun, run.id)
        r.attachment_text = "과제명: 복숭아 선발모형 개발\n" + "본문 " * 5000
        r.attachment_name = "계획서.pdf"
        await s.commit()
    provider = ScriptedProvider([
        '{"title": "복숭아 선발모형 개발", "goals": ["선발모형 개발"], "targets": ["복숭아"], "methods": ["표현형 DB"], "outputs": ["모형"], "prior_work": [], "keywords": ["복숭아", "선발모형"]}',
        '{"sub_queries": ["복숭아 선발모형", "사과 육종 데이터베이스"]}',
        '{"sub_queries": []}',
        "## 1. 검토 대상 요약\n복숭아 [1]\n\n## 요약\n보통",
    ])
    hits = {"복숭아 선발모형": [ev("a", "d1", "s", 0.9)], "사과 육종 데이터베이스": [ev("b", "d2", "s", 0.8)]}

    async def retrieve(q, k):
        return hits.get(q, [])

    result = await _run(test_sessionmaker, run.id, provider, retrieve)
    assert result.status == "done"
    assert result.scope["title"] == "복숭아 선발모형 개발" and result.scope["keywords"] == ["복숭아", "선발모형"]
    stages = [s["stage"] for s in result.steps]
    assert stages[:2] == ["scoping", "scoped"]
    planner_system, planner_user = provider.calls[1][0][0].content, provider.calls[1][0][1].content
    assert planner_system.rstrip().endswith("- 관점 예시: 산출물, 검증 방법")
    assert "검토 대상 요약" in planner_user and "본문 본문 본문" not in planner_user
    synthesis_user = provider.calls[3][0][1].content
    assert "## 검토 대상 요약 (첨부에서 추출)" in synthesis_user and "## 첨부 자료 내용" in synthesis_user


def test_group_by_document_keeps_a_documents_chunks_together():
    from app.research.service import group_by_document

    ranked = [ev("1", "d1", "a", 0.9), ev("2", "d2", "a", 0.8), ev("3", "d1", "b", 0.7), ev("4", "d3", "a", 0.6)]
    assert [e.metadata["chunk_id"] for e in group_by_document(ranked)] == ["1", "3", "2", "4"]


def test_scope_digest_and_scope_prompt_registered():
    from app.chat.prompt import _FALLBACK_PROMPTS
    from app.research.prompts import SCOPE_SYSTEM_PROMPT, planner_messages, scope_digest

    assert _FALLBACK_PROMPTS["research_scope"].text == SCOPE_SYSTEM_PROMPT
    assert scope_digest(None) == "" and scope_digest({"goals": []}) == ""
    d = scope_digest({"title": "T", "goals": ["g1", " "], "keywords": ["k"]})
    assert d == "- 과제명: T\n- 연구 목표: g1\n- 핵심어: k"
    assert planner_messages("BASE", "", "q") == ("BASE", "q")
    assert planner_messages("BASE\n", "힌트", "q")[0] == "BASE\n- 힌트"


def test_templates_carry_the_full_duplication_manual():
    from app.research.templates import TEMPLATES, get_template

    dup = get_template("duplication")
    assert dup is not None and len(dup.instructions) > 10_000
    assert "개선방향 종합표" in dup.instructions and "세부과업별 조정안" in dup.instructions
    assert {t.id for t in TEMPLATES} == {"duplication", "discovery", "proposal"}
    assert get_template("nope") is None


async def test_templates_and_pdf_endpoints(client, db, test_sessionmaker):
    await client.post("/api/auth/register", json={"email": "adm@example.com", "password": "pw123456"})
    from sqlalchemy import select

    admin = await db.scalar(select(User).where(User.email == "adm@example.com"))
    admin.role = "admin"
    await db.commit()
    await client.post("/api/auth/login", json={"email": "adm@example.com", "password": "pw123456"})

    templates = (await client.get("/api/research/templates")).json()
    assert [t["id"] for t in templates] == ["duplication", "discovery", "proposal"]

    created = await client.post("/api/research/projects", json={"name": "중복성", "template_id": "duplication"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["template_id"] == "duplication" and body["planner_hint"].startswith("관점 예시")
    assert "개선방향 종합표" in body["instructions"] and body["instruction_version"] == 1
    assert (await client.post("/api/research/projects", json={"name": "x", "template_id": "nope"})).status_code == 400

    # PDF: 보고서가 있는 실행만, 출처 부록 포함
    async with test_sessionmaker() as s:
        run = ResearchRun(
            project_id=uuid.UUID(body["id"]), prompt="q", created_by=admin.id, status="done",
            report="## 결과\n중복 [1]", sources=[{"index": 1, "filename": "d1.pdf", "page": 3, "snippet": "x"}],
        )
        s.add(run)
        await s.commit()
        rid = run.id
    pdf = await client.get(f"/api/research/runs/{rid}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:4] == b"%PDF" and "filename*=UTF-8''" in pdf.headers["content-disposition"]
    assert (await client.get(f"/api/research/runs/{uuid.uuid4()}/pdf")).status_code == 404


def test_room_instructions_replace_the_generic_synthesis_base():
    """지침이 있으면 base(범용 구성)는 프롬프트에 없어야 한다 - 있으면 모델이 그 구성을 따른다(실사고)."""
    from app.research.prompts import EVIDENCE_RULES, synthesis_messages

    system, user = synthesis_messages("BASE 범용 구성", "# 중복성 매뉴얼\n## 10. 종합 보고서 구성", "요청", "[1] 근거")
    assert system.startswith("# 중복성 매뉴얼") and "BASE" not in system and EVIDENCE_RULES in system
    assert synthesis_messages("BASE 범용 구성", "  ", "요청", "[1]")[0].startswith("BASE 범용 구성")
    assert user.startswith("## 사용자 요청\n요청")
