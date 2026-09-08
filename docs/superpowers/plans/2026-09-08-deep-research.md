# MOPAN — 딥 리서치 (Slice 8) — Implementation Plan

> Spec: 새싹이(saessagi-Linux, `src/deep_research/*`)에 구축된 딥 리서치를 MOPAN에 이식한다. 소유자 요구 2026-09-08. Scope: backend + frontend, 3단계.
> 원본 조사: `saessagi-Linux/src/deep_research/{service,prompts,store,seeds,pdf}.py`, `src/app/deep_research_routes.py`, `web/src/components/DeepResearchView.tsx`, `tests/deep_research/*`(약 85건), `docs/CHANGE_REQUESTS.md` CR-62·CR-65·E-96.
> **주의: 소유자가 "NHNcloud에 구축한 것"이라 부르는 `nhncloud/main`(6f63d5c)은 로컬 `web-ui-mode`(8627f59)보다 버그 수정 3건이 뒤에 있다.** 이식 기준은 로컬 트리다(아래 "원본과의 차이").

## The one sentence this slice is about

**딥 리서치는 "긴 질문에 대한 긴 답"이 아니라, 코퍼스를 여러 갈래로 읽고 빈 곳을 한 번 더 찌른 뒤 읽은 것만 인용하는 보고서다.** 새싹이가 지킨 두 계약을 그대로 가져온다: 인터넷을 부르지 않고 등록된 문서만 근거로 쓴다(오프라인 원칙, `CHANGE_REQUESTS.md:1366`), 그리고 **출처 목록에는 모델이 실제로 읽고 본문에 인용한 근거만 나간다**(`_cited_numbers`, `test_service.py`). 이 둘이 깨지면 슬라이스는 무효다.

## 원본의 모양 (조사 요약)

| 단계 | 하는 일 | LLM 호출 |
|---|---|---|
| 계획 | 질문 → 하위 질의 1~12개(기본 6) JSON | 1회, temp 0.3 |
| 검색 | 질의마다 하이브리드 검색 top_k(기본 5), 문서 단위 dedup | 없음 |
| 격차 분석 | 근거 20건 요약을 보고 보완 질의 ≤3개, `gap_rounds`(기본 1)회, 신규 0건이면 조기 종료 | 라운드당 1회 |
| 종합 | 근거 전부(문자 상한 90,000)를 넣고 한국어 마크다운 보고서, max_tokens 8192 | 1회 |
| 완료 | `{report, sources[{n, doc, page, score}], sub_queries}`, 본문이 인용한 번호만 sources로 | — |

- 실행은 **요청 수명 = 잡 수명**인 SSE 스트림(`deep_research_routes.py:31`). 연결이 끊기면 죽는다. 동시 실행은 `asyncio.Lock` 1개 + 대기열 5, 900초.
- "방(project)" 단위로 지침(`instructions`, 버전 이력)·검색 예산(sub_queries, top_k, gap_rounds, max_evidence_chunks)·대화 턴(steps_json 진행 로그 포함)을 SQLite에 저장(`store.py:51`). 모드 3개를 하드코딩하던 것을 CR-62에서 방 체계로 일반화했다.
- 프롬프트: `EVIDENCE_RULES`·`OUTPUT_FORMAT_RULES`는 편집 불가 안전장치로 항상 뒤에 붙고, 방의 지침만 사용자가 고친다(`prompts.py:154`).
- 입력: 프롬프트 + 선택 첨부(텍스트만 추출, 벡터 등록 안 함) + 선택 범위(문서 id). 출력: 마크다운 + PDF(`fpdf2`, 나눔고딕 임베딩, 브라우저 인쇄는 iOS에서 기각).
- 자기인용 순환 차단: 리서치 산출물(노트)은 검색 대상에서 뺀다(E-96).

### 원본과의 차이 (로컬이 앞선 3건 — 반드시 포함)
1. 근거 문자 상한 14,000 → 90,000 (14건만 읽고 26건을 출처에 올리던 누수)
2. 종합 max_tokens 4,096 → 8,192 (보고서 절단)
3. `_cited_numbers()` — 본문이 인용한 근거만 출처로 (출처 부풀리기 제거)

## Decisions

**실행은 arq 잡이다. 요청 수명에 묶지 않는다.**
소유자는 아이폰으로 Cloudflare 터널을 통해 쓴다. 5~10분짜리 작업을 SSE 연결 하나에 묶으면 화면을 벗어나는 순간 죽는다(원본의 알려진 약점). MOPAN에는 이미 문서 처리용 arq 워커와 Redis가 있다. 리서치 잡은 `research_runs` 행을 만들고 arq에 넣으며, 진행은 잡이 `steps`(JSONB)를 갱신하고 프론트는 2초 폴링으로 읽는다. 원본이 CR-65에서 진행 로그를 DB(`steps_json`)에 두기로 한 결정과 같은 방향이고, 폴링이면 탭을 닫고 돌아와도 그대로 이어 본다. SSE는 폴링 위에 나중에 얹을 수 있지만 폴링 없이 SSE만은 안 된다.

**검색 계층은 MOPAN의 `hybrid_search`를 그대로 쓴다. 새 검색기를 만들지 않는다.**
원본은 GraphRAG(Neo4j)+LanceDB였고 MOPAN은 dense+sparse+접기 융합이다. 원본 `service.py`가 검색기에 요구하는 것은 `retrieve(query, top_k, scope) → [chunk]` 하나다. `app/retrieval/service.py:hybrid_search`를 `top_n=top_k`, `collection_ids/folder_ids`(Slice 7)로 부르면 된다. 검색 품질 개선(재정의, 접기, 재시도)이 자동으로 리서치에도 적용된다. **리서치 산출물은 코퍼스에 넣지 않으므로** E-96의 자기인용 차단은 구조적으로 성립한다.

**모델과 추론 수준은 단계별로 다르다.**
계획·격차 분석·종합은 사용자가 방에서 고른 모델(기본 ANSWER_MODEL)로, 종합만 추론 수준을 받는다(방 설정, 기본 `high` — 리서치가 추론 모델의 존재 이유다). 검색 단계는 LLM을 부르지 않는다. GPT-5.6 계열의 제약(2026-09-08 `adapt_reasoning_effort`)은 프로바이더 한 곳이 이미 처리한다. 격차 분석은 값싼 모델(`QUERY_EXPANSION_MODEL`)로도 충분하다는 것이 원본 측정이므로 방 설정 `gap_model`(비면 질문 다시 쓰기 모델)로 둔다.

**프롬프트 세 개는 프롬프트 저장소에 넣는다. 안전 규칙은 코드에 남긴다.**
`research_planner`, `research_gap`, `research_synthesis`를 `_FALLBACK_PROMPTS`에 등록해 프롬프트 관리에서 편집한다(2026-09-08부터 내장 이름도 목록에 나온다). 원본의 `EVIDENCE_RULES`·`OUTPUT_FORMAT_RULES`는 편집 불가 상수로 항상 뒤에 붙인다 — 원본과 같은 이유로, 인용 강제를 화면에서 지울 수 있게 두지 않는다. 방의 `instructions`는 원본처럼 방마다 버전 이력을 갖는다(프롬프트 저장소의 `version`/`is_active` 패턴 재사용).

**저장은 Postgres 세 표다. SQLite는 가져오지 않는다.**
`research_projects`(방: name, description, budget JSONB, model, reasoning_effort, gap_model, collection_ids, folder_ids, created_by), `research_instructions`(project_id, version, text, is_active, created_by, created_at — 부분 유니크 `is_active`), `research_runs`(project_id, prompt, attachment_text, status, steps JSONB, report, sources JSONB, sub_queries JSONB, error, started_at, finished_at, created_by, model, prompt_versions). 원본의 `turns`는 `research_runs`에 대응하고, 첨부는 텍스트만 저장한다(원본과 같음 — 벡터 등록 없음).

**예산 상한은 원본 값을 그대로 쓰되, 문자 상한 대신 토큰 예산으로 바꾼다.**
sub_queries 1~12(기본 6), top_k 1~15(5), gap_rounds 0~3(1), max_evidence_chunks 5~80(24). 근거 문자 90,000은 MOPAN의 토큰 세기(`count_tokens`)로 `RESEARCH_EVIDENCE_TOKEN_BUDGET`(기본 60,000, 런타임 설정)로 옮긴다. 상한을 넘으면 점수 순으로 자르고 잘린 개수를 steps에 적는다(원본 누수 버그의 재발 방지 — "읽지 않은 것은 출처가 아니다").

**동시 실행은 사용자당 1, 전체 2.**
arq 잡 큐가 대기열을 대신한다. `research_runs`에 같은 사용자의 `running`이 있으면 409. 워커의 리서치 큐는 별도 큐 이름(`research`)으로 문서 처리와 경합하지 않게 한다.

**PDF는 서버에서 만든다. 폰트가 없으면 명확히 실패한다.**
원본 `pdf.py`(fpdf2, 표 헤더 반복, 인용 마커 제거)를 가져온다. 이 HPC 노드에 나눔고딕이 없으면 `PdfUnavailable`(503)과 함께 설치 안내를 낸다. 브라우저 인쇄는 원본이 iOS에서 기각했으므로 다시 시도하지 않는다.

**첨부 입력은 MOPAN의 첨부 파서를 재사용한다.**
채팅 첨부(`app/attachments`)가 이미 PDF·이미지 텍스트 추출을 한다. 리서치 첨부도 같은 경로로 텍스트를 뽑아 `attachment_text`로 넣고, 3만 자 절단(원본 값)은 토큰 예산 안에서 우선 배정한다.

**버려진 대안.** 웹 검색 추가 — 오프라인 원칙을 깨고 인용 검증을 불가능하게 한다. 원한다면 MCP 도구(예: 외부 검색 서버)를 방 설정에서 켜는 방식으로 후속 슬라이스에 둔다. 채팅 안의 "딥 리서치 모드" — 5분 걸리는 작업을 채팅 말풍선에 넣으면 진행·이력·PDF가 다 어색해진다. 원본도 별도 화면이다.

## The API contract the frontend builds against

```
GET    /api/research/projects                          → [{id, name, description, budget, model, reasoning_effort, collection_ids, folder_ids, last_run_at, run_count}]
POST   /api/research/projects                          {name, description?, instructions?, budget?, model?, reasoning_effort?, collection_ids?, folder_ids?}  admin 201
PATCH  /api/research/projects/{pid}                    같은 필드 부분 갱신                                  admin
DELETE /api/research/projects/{pid}                    admin (실행 이력도 CASCADE)
GET    /api/research/projects/{pid}/instructions       → [{version, text, is_active, created_by_email, created_at}]
POST   /api/research/projects/{pid}/instructions       {text}  → 새 버전 활성                                admin
POST   /api/research/projects/{pid}/instructions/{v}/activate                                                 admin

POST   /api/research/projects/{pid}/runs               multipart: prompt, file?  → 202 {run_id}   (같은 사용자 running 있으면 409)
GET    /api/research/runs/{rid}                        → {status: queued|planning|searching|gap|synthesis|done|failed,
                                                          steps:[{stage, at, detail}], report?, sources?, sub_queries?, error?}
GET    /api/research/projects/{pid}/runs?limit&offset  → {total, items:[run 요약]}
POST   /api/research/runs/{rid}/cancel                 → 202 (잡이 다음 단계 경계에서 멈춤)
POST   /api/research/runs/{rid}/pdf                    → application/pdf (report 렌더; 폰트 없으면 503 안내)
```

`sources` 항목: `{n, chunk_id, document_id, filename, page, section, score}` — 채팅의 `Citation`과 같은 모양이라 `MessageBubble`의 인용 렌더러와 출처 시트를 그대로 재사용한다.

## 단계와 태스크

### 1단계 — 백엔드 파이프라인과 API

**Task 1.1: 마이그레이션 `0019_research`** (2026-09-08 적용됨). 위 세 표. `research_runs.steps`는 JSONB 배열, `status` CHECK.

**Task 1.2: `app/research/service.py` — 원본 `service.py:_run_pipeline` 이식.**
```python
async def run_pipeline(run: ResearchRun, *, project, llm, retrieve, settings, progress) -> None:
    # 단계 경계마다 progress(stage, detail)를 DB에 적고 cancel 플래그를 본다.
    plan = await plan_queries(llm, project, run.prompt, run.attachment_text)          # 실패 → [run.prompt] 단일 질의 폴백(원본 계약)
    evidence = await search_all(retrieve, plan.sub_queries, budget.top_k, scope)      # 문서 단위 dedup, 청크 중복 병합
    for _ in range(budget.gap_rounds):
        extra = await gap_queries(llm_cheap, run.prompt, digest(evidence[:20]))       # ≤3개
        new = await search_all(retrieve, extra, budget.top_k, scope)
        if not merge_new(evidence, new): break                                        # 신규 0건이면 조기 종료
    evidence = clamp_to_budget(evidence, settings.research_evidence_token_budget)    # 잘린 개수를 steps에
    if not evidence: return finish(run, NO_EVIDENCE_REPORT, sources=[])               # 근거 0건이면 종합 생략(원본 계약)
    report = await synthesize(llm, project, run.prompt, evidence, reasoning_effort)
    cited = cited_numbers(report)                                                     # 본문 [n]만
    finish(run, report, sources=[evidence[n-1] for n in cited])
```
`retrieve`는 `hybrid_search`를 감싼 클로저(`collection_ids`, `folder_ids`, `top_n=top_k`). `llm_cheap`은 `gap_model`.

**Task 1.3: 프롬프트.** `app/chat/prompt.py`의 `_FALLBACK_PROMPTS`에 `research_planner`(JSON 스키마 강제, 원본 `PLANNER_JSON_SCHEMA`), `research_gap`, `research_synthesis`(원본 `GENERIC_SYNTHESIS`) 등록. `app/research/prompts.py`에 편집 불가 `EVIDENCE_RULES`·`OUTPUT_FORMAT_RULES`·`NO_EVIDENCE_REPORT`. 방 지침은 `research_synthesis` 뒤, 안전 규칙 앞에 삽입.

**Task 1.4: arq 잡 `app/worker.py:run_research`.** 큐 이름 `research`, `WorkerSettings`에 함수 등록, `max_jobs` 2. 잡은 `research_runs`를 잠그고 상태를 옮기며, 예외는 `failed`+`error`로 적는다(원본: 실패가 조용히 사라지지 않는다). `cancel`은 행의 `cancel_requested`를 보고 단계 경계에서 멈춘다.

**Task 1.5: 라우터 `app/research/router.py`** — 위 계약. 방 CRUD는 admin, 실행·조회는 로그인 사용자(원본과 같이 사용자도 리서치를 돌린다). 실행 시 첨부는 `app/attachments`의 파서로 텍스트만.

**Task 1.6: 테스트 이식 `tests/test_research_*.py`** — 원본 `test_service.py`의 계약을 옮긴다: 단계 순서, **읽은 근거 수 = 출처 수**, 본문 인용된 것만 출처, 계획 실패 시 단일 질의 폴백, 근거 0건이면 종합 생략, gap_rounds=0 스킵, 범위 필터가 검색에 도달, 지침이 LLM 호출에 도달하고 안전 규칙이 살아남음, 토큰 예산 초과 시 잘린 개수 기록, 동시 실행 409. FakeProvider는 `tests/test_intent.py`의 것을 재사용.

### 2단계 — 화면

**Task 2.1: 사이드바 "딥 리서치" 항목** (워크플로우 아래). 관리자가 아니어도 보인다.

**Task 2.2: 방 목록 `/research`.** 카드(이름·설명·마지막 실행·실행 수), "새 방"(admin). 원본 시드 3개(중복 검토/탐색/제안)는 **넣지 않는다** — MOPAN 코퍼스는 특허·상표 심사기준이라 원본의 농업 예시가 맞지 않고, 빈 방 목록의 첫 카드에 "예: 상표 출원 전 검토, 규정 개정 영향 분석" 안내문만 둔다.

**Task 2.3: 방 화면 `/research/[pid]`.** 상단: 이름, 범위 칩(분류/폴더, `@` 멘션 메뉴 재사용), 모델·추론 수준(모델 선택 시트 재사용). 접힌 "지침" 카드(현재 버전 텍스트, 편집 → 새 버전, 이력). 접힌 "검색 예산" 카드(슬라이더 4개, 각 도움말은 원본 문구 번역). 본문: 프롬프트 입력(첨부 클립 포함) + "리서치 시작". 아래에 실행 이력 목록.

**Task 2.4: 실행 화면 `/research/runs/[rid]`.** 세로 진행 표시(계획 → 검색 n/6 → 격차 분석 → 종합, 원본의 stage 이벤트를 steps로 그림), 2초 폴링, 취소 버튼. 완료 시 보고서를 `Markdown` 컴포넌트로(인용 `[n]` 클릭 → 출처 시트, `MessageBubble`과 같은 코드), 출처 목록, "PDF 저장", "복사". 실패 시 `error`와 "다시 실행". 모바일: 진행 표시는 한 줄 요약 + 펼치기, 보고서는 본문 폭 그대로.

**Task 2.5: 상태 유지.** 실행 중 다른 화면으로 가도 사이드바 항목에 점(●)으로 진행 중을 표시(폴링은 방 화면에서만). 돌아오면 이어서 보인다 — arq 잡이므로 가능.

### 3단계 — PDF, 첨부, 폴더 범위, 운영 설정

**Task 3.1: PDF.** 원본 `pdf.py`·`pdf_blocks` 이식(`fpdf2` 의존성 추가). 폰트 탐색 순서: `/usr/share/fonts/truetype/nanum/NanumGothic.ttf` → `fonts/` 리포 폴더(운영자가 넣음). 없으면 503과 함께 `apt install fonts-nanum` 안내. 원본 `test_pdf*.py` 이식(표 헤더 반복, 인용 마커 제거, 폰트 부재 오류).

**Task 3.2: 폴더 범위.** Slice 7이 배포되면 방의 `folder_ids`가 `retrieve` 범위에 들어간다. 그 전에는 `collection_ids`만.

**Task 3.3: 고급 설정 "딥 리서치" 카테고리.** 런타임 설정: `RESEARCH_EVIDENCE_TOKEN_BUDGET`(60,000), `RESEARCH_MAX_CONCURRENT`(2), `RESEARCH_DEFAULT_REASONING_EFFORT`(high). 환경변수 전용: `RESEARCH_PDF_FONT_PATH`.

**Task 3.4: 관측.** `log_event("research_run_finished", project, ms, sub_queries, evidence_read, cited, tokens)`. 실행 화면의 "추적" 버튼이 채팅의 추적 시트처럼 steps와 프롬프트 버전을 보인다.

## 사용자·관리자 관점 점검표 (완료 기준)

- 사용자: 방을 열고 질문을 적고 시작하면, 탭을 닫았다가 10분 뒤 돌아와도 결과가 있다. 보고서의 모든 `[n]`은 눌러서 원문 청크를 볼 수 있고, 출처 목록에는 본문에 없는 번호가 없다. PDF는 아이폰 사파리에서도 열린다.
- 관리자: 방마다 지침·예산·범위·모델을 정하고 버전 이력을 본다. 프롬프트 관리에서 계획·격차·종합 프롬프트를 고치면 다음 실행부터 반영된다. 실패한 실행의 원인이 화면에 적혀 있다.
- 회귀: 채팅 경로는 한 줄도 바뀌지 않는다(공유하는 것은 `hybrid_search`, 프롬프트 저장소, 첨부 파서, 인용 렌더러다).

## 위험과 대비

- **비용.** 하위 질의 6개 × top_k 5 + 격차 1라운드 = 검색 30~45회(임베딩 호출 6~9회) + LLM 3회(종합 1회가 근거 60k 토큰). Terra 깊이 추론이면 한 번에 수 분·수천 원 단위다. 방의 예산 기본값을 원본 그대로 두고, 실행 화면에 토큰 사용량을 적는다.
- **워커 경합.** 문서 처리와 같은 워커 프로세스면 큐 이름을 나눠도 CPU를 나눈다. 리서치 워커를 `start_local.sh`에서 별도 프로세스로 띄운다.
- **폰트.** 이 노드에는 나눔스퀘어(`/usr/share/fonts/truetype/nanum/NanumSquare*.ttf`)와 Noto Serif CJK가 있다(2026-09-08 `fc-list` 확인). 원본이 쓰던 NanumGothic.ttf는 없으므로 폰트 탐색 순서에 NanumSquare를 넣는다. 다른 배포에서는 `fc-list | grep -i nanum`으로 확인한다.
- **원본 회귀.** `nhncloud/main`이 아니라 로컬 트리를 기준으로 삼는다. 이식 전에 새싹이 로컬의 수정 커밋(8627f59)을 nhncloud에 푸시해 두 저장소를 맞추는 것이 안전하다.

## 순서와 규모

1단계 3일(서비스 이식 1.5, 잡·API·테스트 1.5), 2단계 3일, 3단계 2일. Slice 7(폴더) 1단계와 병행 가능하며, 폴더 범위 연동만 Slice 7 뒤에 온다.
