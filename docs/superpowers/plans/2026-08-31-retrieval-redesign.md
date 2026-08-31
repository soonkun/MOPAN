# Plan: 검색 파이프라인 재설계

Spec: `docs/superpowers/specs/2026-08-31-retrieval-redesign.md`.

이 계획은 `backend/app/retrieval/**`, `backend/app/chat/prompt.py`,
`backend/app/core/config.py`, `backend/app/core/settings_store.py`,
`backend/app/models/chunk.py`, `scripts/eval_retrieval.py`를 다룬다.
`frontend/**`는 다른 에이전트의 것이고 이 계획은 아무 말도 하지 않는다.

원칙: **한 번에 한 단계, 같은 52문항, 누적 측정.** 값을 못 하는 단계는 기본 OFF로
배포하고 그 사실을 분명히 적는다.

---

## Task 139 — 베이스라인 고정

- [x] **Step 139a: Measure the shipped pipeline on the 52-question fixture, arms**
      decomposed (dense-only / sparse-only / fused). 결과는 spec §1.2.
- [x] **Step 139b: Measure the sparse 2×2 (tokenizer × scorer) so a gain can be**
      attributed. 결과는 spec §1.3.
- [x] **Step 139c: Verify `scripts/eval_questions_ko.json` 의 모든 질문이 사람이**
      칠 법한 문장인지. 맨명사 질의는 0건이어야 한다.
- [x] **Step 139d: Modify `scripts/eval_retrieval.py` — 계측 도구를 재설계한다.**
      모든 행이 하나의 파이프라인 구성이고, `--arm` x `--dense` x `--sparse` x
      `--unit` x `--expand` x `--rerank` x `--expansion` 의 교차곱이다. 여기서 끌
      수 없는 단계는 제품에서도 끌 수 없다. 그룹별 채점, 지연과 비용 열을 함께 낸다.
- [x] **Step 139e: Modify `scripts/check_all_plans.py` — 이 계획을 등록한다.**

베이스라인: **anchor@14 = 0.846** (base .857 / neighbor .875 / tokenizer .750 /
proviso .900 / crossref .800), prec 0.195, 24.0 ms/q, $0.0000010/q.

## Task 140 — 설정 노브와 RRF 타이브레이크

- [ ] **Step 140a: Modify `backend/app/retrieval/rrf.py` — 명시적 2차 정렬 키(첫**
      등장 순서)를 넣어 2N개 순위목록에서도 동점 처리가 결정적이게 만들고,
      docstring이 코드가 실제로 하는 일을 말하게 고친다.
- [ ] **Step 140b: Modify `backend/app/core/config.py` — `SPARSE_TOKENIZER`,**
      `RERANK_MODEL`, `RERANK_TIMEOUT_SECONDS`, `QUERY_EXPANSION_COUNT`,
      `QUERY_EXPANSION_MODEL`, `QUERY_EXPANSION_TIMEOUT_SECONDS`,
      `CLARIFY_ON_WEAK_EVIDENCE` 를 측정값 주석과 함께 둔다.
- [ ] **Step 140c: Modify `backend/app/core/settings_store.py` — 배선되지 않은**
      단계를 설명하는 문장을 사실로 고친다 (`RETRIEVAL_CANDIDATE_LIMIT`의 "재순위
      모델이 점수를 매기는 대상이기도 합니다").

## Task 141 — sparse arm: 문자 bigram 토큰

- [ ] **Step 141a: Create `backend/app/retrieval/tokenize.py` — 인제스트와 질의가**
      **같은** 순수 함수를 통과하도록 하는 토크나이저. `simple`과 `bigram` 두 개.
- [ ] **Step 141b: Modify `backend/app/models/chunk.py` — `content_tsv`를 생성**
      컬럼에서 애플리케이션이 쓰는 컬럼으로 바꾼다.
- [ ] **Step 141c: Create a migration turning the generated column into a plain**
      one, with a working downgrade that restores the generated expression.
      head는 `0011`.
- [ ] **Step 141d: Modify `backend/app/rag/pipeline.py` — 청크를 쓸 때 tsvector도**
      함께 쓴다.
- [ ] **Step 141e: Modify `backend/app/retrieval/keyword_search.py` — 질의 측을**
      같은 토크나이저로 바꾼다. 전부 불용어인 질의의 **기권**은 유지하고, 제거된
      불용어 `방법은`/`이`는 계속 제거된 채로 둔다.
- [ ] **Step 141f: Create a backfill script for the 2578 existing chunks.**
- [ ] **Step 141g: Measure the fused pipeline. 기대: 0.846 → 0.962.**

## Task 142 — dense arm: `text-embedding-3-large` @ 1536

- [ ] **Step 142a: Modify `backend/app/llm/openai_provider.py` — `dimensions`를**
      임베딩 호출에 넘긴다. 이것이 마이그레이션을 없애는 유일한 이유다.
- [ ] **Step 142b: Modify `.env.example` — 모델과 차원, 그리고 이 둘이 왜 env**
      전용인지(저장된 벡터 2578개가 무효가 된다).
- [ ] **Step 142c: Run the re-embed over the live corpus. $0.121 one-off.**
- [ ] **Step 142d: Measure . 기대: +0.058.**

## Task 143 — 재순위: 껍데기 삭제, `Reranker | None`

- [ ] **Step 143a: Modify `backend/app/retrieval/reranker.py` — `NoneReranker`를**
      **삭제**하고 `LLMReranker`와 `make_reranker`를 둔다. `make_reranker`는
      `RERANK_MODEL=""`에서 `None`을 돌려준다.
- [ ] **Step 143b: Modify `backend/app/retrieval/service.py` — `Reranker | None`.**
      `None`이면 그 단계가 호출 경로에 없다.
- [x] **Step 143c: Modify `backend/app/chat/router.py` — 네 호출 지점.**
- [x] **Step 143e: Modify `backend/app/workflow/executor.py` and `backend/app/workflow/tools.py` — `Reranker`를 들고 있는 나머지 두 자리를 `Reranker | None`으로 넓힌다.**
- [x] **Step 143f: Modify `backend/tests/test_neighbors.py` and `backend/tests/test_workflows.py` — `NoneReranker()`를 `None`으로 바꾼다. 이 테스트들이 늘 뜻하던 것이 "재순위 단계 없음"이었다.**
      `make_reranker`가 `None`을 돌려줄 수 있으므로 호출부의 타입이 바뀐다.
- [ ] **Step 143d: Measure rerank × candidate_limit (20/40/60). 값을 못 하면 OFF.**

## Task 144 — 질의 확장

- [ ] **Step 144a: Create `backend/app/retrieval/expansion.py` — N개 변형을 쓰고,**
      **모든 변형이 두 arm 모두에** 들어간다. 실패·지연은 원 질의로 강등.
- [ ] **Step 144b: Modify `backend/app/retrieval/service.py` — 2N개 순위목록.**
- [ ] **Step 144c: Modify `backend/app/chat/service.py` — 모든 직접 RAG 호출자가**
      지나는 하나의 길목에서 새 단계들을 옵트인한다. 네 개의 호출 지점이 각자
      기억해야 하는 구조로 만들지 않는다.
- [ ] **Step 144d: Measure . 값을 못 하면 OFF.**

## Task 145 — 근거 부족 분기

- [ ] **Step 145a: Modify `backend/app/chat/prompt.py` — 되묻기가 곧 답변이다.**
      새 엔드포인트도 새 UI도 없다.
- [ ] **Step 145b: Measure the false-trigger rate on the 52 well-formed**
      questions. 작지 않으면 검출기가 틀린 것이고, 틀린 검출기는 지금의 막다른
      길보다 나쁘다.

## Task 146 — 검증과 배포

- [ ] **Step 146a: Run the backend test suite, one session, no `-n auto`.**
- [ ] **Step 146b: Run `docker compose build backend worker` then**
      `docker compose up -d --force-recreate --no-deps backend worker`.
      `up -d --build`는 여기서 두 번 컨테이너를 교체하지 않고 이미지만 만들었다.
- [ ] **Step 146c: Verify `/api/search` for the 상표등록출원 case through the live**
      stack, and record the p573 chunk's rank.
- [ ] **Step 146d: Run `python scripts/eval_retrieval.py --drop-lex` to remove the**
      throwaway eval tables from the live database.
