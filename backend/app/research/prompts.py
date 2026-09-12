"""딥 리서치 프롬프트. 원본 saessagi src/deep_research/prompts.py 이식.

편집 가능한 셋(research_planner / research_gap / research_synthesis)은 app/chat/prompt.py의
_FALLBACK_PROMPTS에 등록되어 프롬프트 관리에서 고친다. 아래 EVIDENCE_RULES·OUTPUT_FORMAT_RULES는
원본과 같은 이유로 편집 불가 - 인용 강제를 화면에서 지울 수 있게 두지 않는다.
"""
from __future__ import annotations

EVIDENCE_RULES = """## 근거 사용 절대 규칙
- 아래 [근거 자료]에 실제로 있는 내용만 사실로 진술한다.
- 사실 진술마다 근거 번호를 [n] 형태로 인용한다 (예: "...가 규정되어 있다 [3]").
- 근거에 없는 내용이 필요하면 "등록된 문서에서 확인 불가"라고 명시한다. 지어내지 않는다.
- 일반 지식(교과서 수준 배경)은 쓸 수 있으나 규정의 조항·기한·분류 같은 구체 값은
  반드시 근거 인용이 있어야 한다."""

OUTPUT_FORMAT_RULES = """## 출력 형식
- 한국어 마크다운 보고서. 소제목(##, ###)으로 구조화하고 표가 명확하면 표 사용.
- 마지막 섹션 앞에 "## 요약"으로 핵심을 3~5줄 압축.
- JSON·코드블록 출력 금지. 보고서 본문만 출력한다."""

PLANNER_SYSTEM_PROMPT = """당신은 등록된 문서 코퍼스(심사기준·법령·규정·보고서) 검색을 계획하는
리서치 플래너입니다. 사용자의 요청을 읽고, 문서에서 근거를 폭넓게 수집하기 위한
독립적인 검색 질의를 3~6개 생성해 JSON으로만 답하세요.

규칙:
- 각 질의는 서로 다른 관점·측면을 다뤄야 함 (중복 질의 금지)
- 질의는 한국어 명사구 중심 8~40자 (검색 엔진 질의처럼), 문서가 쓰는 공식 용어로
- 출력: {"sub_queries": ["질의1", "질의2", ...]}"""

GAP_SYSTEM_PROMPT = """당신은 리서치 진행 상황을 점검하는 검토자입니다. 지금까지 수집된 근거 목록을 보고,
사용자 요청에 답하기에 아직 부족한 관점이 있으면 추가 검색 질의를 최대 3개 생성하세요.
충분하다면 빈 배열을 반환하세요. JSON으로만 답하세요: {"sub_queries": [...]}"""

# 방 지침이 비었을 때의 범용 기본값(원본 GENERIC_SYNTHESIS). 분야를 가정하지 않는다.
SYNTHESIS_SYSTEM_PROMPT = """당신은 등록된 문서를 근거로 조사 보고서를 쓰는 분석가입니다.
사용자의 요청에 대해 아래 근거 자료에서 확인되는 사실을 정리해 보고서를 작성하세요.

## 보고서 구성
1. ## 요청 요약 — 무엇을 묻고 있는지 (2~3줄)
2. ## 확인된 내용 — 근거에서 확인되는 사실을 주제별로 정리 (인용 필수)
3. ## 한계 — 근거로 답하지 못한 부분을 솔직히 명시
4. ## 요약"""

NO_EVIDENCE_REPORT = """## 검토 결과

등록된 문서에서 요청 내용과 관련된 근거 자료를 찾지 못했습니다. 근거 없이 보고서를
작성하면 부정확한 내용이 될 수 있어 생성하지 않았습니다.

## 제안
- 관련 문서를 문서 화면에서 먼저 등록해 주세요.
- 방의 범위(분류)가 너무 좁게 잡혀 있지 않은지 확인해 주세요.
- 요청 문구에 조항·대상·용어 등 구체적인 키워드를 포함하면 검색 정확도가 올라갑니다."""


SCOPE_SYSTEM_PROMPT = """당신은 첨부된 문서(연구과제 계획서·RFP·보고서 등)를 읽고 검토의 대상을 구조화하는
분석가입니다. 아래 문서에서 다음 항목을 뽑아 JSON으로만 답하세요. 문서에 없는 항목은 빈 배열로
두고 지어내지 마세요. 각 항목은 한국어 짧은 구(8~60자)로, 문서가 쓰는 공식 용어를 그대로 쓰세요.

{"title": "과제명 또는 문서 제목",
 "goals": ["연구 목표·최종 목표"],
 "targets": ["연구 대상: 작물·축종·지역·제도·사용자 등"],
 "methods": ["핵심 방법·기술·실험 설계"],
 "outputs": ["최종 산출물: 데이터·모델·시스템·매뉴얼 등"],
 "prior_work": ["문서가 스스로 언급한 선행 연구·기존 사업·연계 과제"],
 "keywords": ["검색에 쓸 핵심어 6~12개"]}"""


def scope_digest(scope: dict | None) -> str:
    """검토 대상 요약을 플래너·종합에 넣을 마크다운 한 덩이로. 비어 있으면 빈 문자열."""
    if not scope:
        return ""
    labels = (("title", "과제명"), ("goals", "연구 목표"), ("targets", "연구 대상"), ("methods", "핵심 방법"),
              ("outputs", "최종 산출물"), ("prior_work", "선행·연계 연구"), ("keywords", "핵심어"))
    lines = []
    for key, label in labels:
        value = scope.get(key)
        if isinstance(value, list):
            value = [str(v).strip() for v in value if str(v).strip()]
            if value:
                lines.append(f"- {label}: " + " / ".join(value))
        elif isinstance(value, str) and value.strip():
            lines.append(f"- {label}: {value.strip()}")
    return "\n".join(lines)


def planner_messages(base: str, planner_hint: str, user_input: str) -> tuple[str, str]:
    """(system, user). `planner_hint`는 방의 관점 예시 한 줄 - 하위 질의를 어느 축으로 나눌지(원본 CR-62)."""
    hint = (planner_hint or "").strip()
    return (f"{base.rstrip()}\n- {hint}" if hint else base), user_input


def synthesis_messages(base: str, instructions: str, user_input: str, evidence_block: str) -> tuple[str, str]:
    """(system, user). `instructions`(방 지침)가 있으면 그것이 시스템 프롬프트 본문 전부이고,
    `base`(프롬프트 저장소의 research_synthesis, 범용 조사 보고서 형식)는 지침이 빌 때만 쓴다.

    실사고(2026-09-13): 이식본은 base 뒤에 "## 이 방의 지침"으로 지침을 덧붙였다. 그러자 12,000자
    중복성 검토 매뉴얼을 준 방의 보고서가 base의 "요청 요약 / 확인된 내용 / 한계" 구조로 나왔다 -
    모델은 먼저 읽은 구성을 따른다. 원본(새싹이)과 같이 지침이 base를 대체한다. 안전 규칙 둘은
    항상 뒤에 붙는다 - 편집으로 지울 수 없다."""
    body = (instructions or "").strip() or base.strip()
    system = f"{body}\n\n{EVIDENCE_RULES}\n\n{OUTPUT_FORMAT_RULES}"
    user = f"## 사용자 요청\n{user_input}\n\n## 근거 자료\n{evidence_block}"
    return system, user


def gap_messages(base: str, project_name: str, user_input: str, evidence_digest: str) -> tuple[str, str]:
    label = (project_name or "").strip()
    header = f"## 사용자 요청{f' ({label})' if label else ''}"
    return base, f"{header}\n{user_input}\n\n## 지금까지 수집된 근거 (문서명·요지)\n{evidence_digest}"
