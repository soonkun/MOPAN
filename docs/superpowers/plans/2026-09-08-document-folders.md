# MOPAN — 폴더형 문서 관리와 규정 현행화 (Slice 7) — Implementation Plan

> Spec: 이 문서가 곧 스펙이다(소유자 요구 2026-09-08 구두). Scope: backend + frontend, 4단계로 나눠 각 단계가 홀로 배포 가능해야 한다.
> 조사 근거: `backend/app/models/{collection,document,chunk}.py`, `backend/app/documents/router.py`, `frontend/app/(app)/documents/page.tsx`, `frontend/lib/mention.ts`, `backend/app/examples_mcp/main.py:490-512`.

## The one sentence this slice is about

**검색은 문서를 "찾는" 수단이고, 폴더는 문서를 "책임지는" 수단이다.** 20년치 보고서 500건과 개정되는 규정을 검색에만 맡기면, 폐기된 규정이 현행 규정과 같은 무게로 근거에 섞인다. 이 슬라이스가 끝나면 관리자는 탐색기처럼 폴더를 열어 문서를 옮기고, 규정을 "새 버전으로 교체"하면 옛 버전은 자동으로 검색에서 빠진다. 이 불변식이 깨지면 슬라이스는 무효다: **`is_current = false`인 문서의 청크는 어떤 검색 경로에도 나오지 않는다(명시적으로 이력을 요청한 경우만 예외).**

## 현재 상태 (조사 요약)

- 컬렉션은 평면 목록이다. `collections`에 `parent_id`가 없고(`models/collection.py:11`), 사이드바 주석이 이미 "a folder, which is what a collection is"라고 자백한다(`Sidebar.tsx:52`).
- 문서는 컬렉션에 1:N으로 매달리고(`documents.collection_id` CASCADE), 파일명 UNIQUE가 없어 같은 파일을 다시 올리면 **새 행이 하나 더 생긴다**(`storage.py:16-19`가 파일명을 경로에 쓰지 않음). 해시·중복 감지 없음.
- `GET /api/documents`는 `collection_id` 필터 하나, 페이지네이션 없음, 전체 반환(`router.py:276`). 문서를 다른 컬렉션으로 옮기는 API가 **없다**. 일괄 작업도 없다(주석 "never a bulk action").
- 검색 범위는 `collection_ids`로만 좁힌다(`vector_store.py:160-176`, `keyword_search.py:193-232`). 채팅의 `@분류명`은 단일 컬렉션 선택이다(`mention.ts:56-92`).
- 내장 표 조회 `table_lookup`은 범위를 무시하고 전 코퍼스를 ILIKE로 훑는다(`examples_mcp/main.py:490-512`). 폴더를 도입하면 여기가 구멍이다.
- 버전 개념은 `WorkflowVersion`과 프롬프트 저장소에만 있다: `version` int + `is_active` 부분 유니크 인덱스(`models/workflow.py:160-213`). 규정 현행화는 이 패턴을 그대로 쓴다.
- Alembic head는 `0019`(0018 모델 레지스트리, 0019 딥 리서치가 2026-09-08에 먼저 들어갔다) → 이 슬라이스는 `0020`(폴더), `0021`(버전·해시)로 시작한다.

## Decisions

**폴더는 컬렉션 안에 있는 트리다. 컬렉션은 그대로 "처리 방식과 권한의 단위"로 남는다.**
컬렉션에 `parent_id`를 넣는 대안은 `workflow_collections`·`collection_ids` 필터·`@` 메뉴가 전부 평면 목록을 전제하므로 세 곳을 재귀로 고쳐야 하고, 청킹 전략(표형/법령형)이 컬렉션마다 하나라는 성질도 깨진다. 컬렉션 안의 폴더는 정리 수단일 뿐이라 청킹·권한을 건드리지 않고, 검색 필터는 "이 폴더 아래 문서 집합"이라는 문서 id 조건 하나만 늘어난다. 사용자 눈에는 "분류 → 폴더 → 폴더 → 문서"의 탐색기 한 그루다.

**폴더 트리는 materialized path로 저장한다. ltree 확장은 쓰지 않는다.**
`folders.path`에 `/{id}/{id}/{id}/`처럼 조상 id를 이어 붙인 문자열을 두고, 하위 트리는 `path LIKE '{prefix}%'` 한 번으로 얻는다. 옮기기는 하위 트리의 path 접두어를 UPDATE 한 번으로 바꾼다. 이 배포의 Postgres는 apt 설치본이라 확장 추가가 배포 절차를 늘린다(pgvector 하나로도 관리자 개입이 필요했다). 깊이 상한 8, 폴더당 이름 UNIQUE(같은 부모 안에서).

**규정 현행화는 "새 버전으로 교체"다. 문서 행을 덮어쓰지 않는다.**
`documents`에 `lineage_id`(같은 규정의 계보), `version`(1부터), `is_current`(계보당 하나만 true, 부분 유니크 인덱스), `superseded_at`, `effective_date`(선택)를 둔다. 새 버전 업로드는 새 문서 행과 새 청크를 만들고 옛 행을 `is_current=false`로 내린다. 옛 파일은 그대로 내려받을 수 있고 청크도 남지만, **검색 두 팔과 표 조회는 기본적으로 `is_current`만 본다**. 덮어쓰기 대안은 "무엇이 바뀌었나"를 잃고, 답변 이력의 `citations`가 가리키던 청크가 사라져 과거 답을 검증할 수 없게 된다. `Message.citations`가 청크 id를 적는 이 프로젝트의 원칙(프롬프트·워크플로우 버전이 이력을 지우지 않는 것과 같은 이유)과 맞추기 위해서다.

**중복은 막지 않고 알려 준다.**
업로드 시 SHA-256을 계산해 `documents.sha256`에 적고, 같은 해시의 `is_current` 문서가 있으면 202 대신 `409 {existing_document_id, filename, folder}`를 돌려준다. 화면은 "같은 파일이 '규정/인사/복무규정.pdf'에 이미 있습니다 — 그래도 올리기 / 그 문서로 이동"을 묻는다. 같은 파일을 다른 폴더에 두려는 정당한 경우가 있어(참조용 사본) 강제 거부는 하지 않는다. `?force=true`로 통과.

**일괄 작업은 이동·삭제·재처리 세 가지, 한 엔드포인트다.**
`POST /api/documents/bulk {ids, action, folder_id?}`. 화면의 체크박스 선택과 짝이다. 기존 주석 "never a bulk action"은 실수 삭제를 막기 위한 것이었으므로, 삭제만은 개수와 폴더를 적은 확인 대화상자를 거치고 되돌리기는 지원하지 않는다(파일도 지운다). 이동은 되돌리기가 곧 다시 이동이라 확인 없이 즉시 실행한다.

**채팅에서 폴더 범위는 `@`로 고른다. 폴더 목록은 검색으로 좁힌다.**
지금 `@분류명`이 `rag:{collection_id}` 행으로 펼쳐진다. 폴더는 `rag:{collection_id}:{folder_id}` 행으로 펼치되, 멘션 메뉴는 폴더가 8개를 넘으면 입력한 글자로 이름·경로를 필터한다. 서버는 `collection_ids`와 함께 `folder_ids`를 받고, 폴더가 오면 하위 트리의 문서 id 집합으로 두 팔을 좁힌다. 워크플로우의 RAG 노드도 같은 두 필드를 갖는다(`tools.py:135-165`).

**내장 표 조회에 범위를 주입한다. 모델이 넣는 인자가 아니다.**
`run_tool_calls`가 builtin 서버(2026-09-08의 `metadata["builtin"]` 표시)를 부를 때 요청의 `document_ids` 범위를 인자에 덧붙인다. `table_lookup`은 그 인자가 있으면 `c.document_id IN (...)`을 추가하고 `is_current`를 항상 조건에 넣는다. 범위를 모델에게 맡기면 모델이 빠뜨린다.

**목록 API는 이 슬라이스에서 페이지네이션·정렬을 받는다.**
`GET /api/documents?collection_id&folder_id&q&status&sort=name|created_at|updated_at|size|chunks&order=asc|desc&offset&limit(≤200)`. 응답은 `{total, items}`(청크 목록과 같은 모양). 500건 폴더를 전체 반환하는 현재 방식은 트리를 넣는 순간 체감이 나빠진다. 클라이언트 필터(파일명·등록자)는 서버 `q`로 옮긴다.

**버려진 대안들.** 태그(라벨) 시스템 — 정리 수단으로는 폴더보다 자유롭지만 "어디에 있나"를 묻는 관리자에게는 한 문서가 여러 곳에 있는 것이 곧 혼란이다. 태그는 나중에 폴더 위에 얹을 수 있고 폴더를 태그 위에 얹기는 어렵다. 컬렉션 간 문서 이동 — 청킹 전략이 다르면 청크를 다시 잘라야 한다. 이동은 같은 컬렉션 안에서만 허용하고, 컬렉션을 바꾸는 것은 "재처리를 동반하는 이동"으로 4단계에 둔다.

## The API contract the frontend builds against

```
# 폴더
GET    /api/collections/{cid}/folders                 → [{id, parent_id, name, path, depth, document_count, child_count}]
POST   /api/collections/{cid}/folders                 {name, parent_id|null}           admin  201
PATCH  /api/folders/{fid}                             {name?, parent_id?}               admin  (parent_id 변경 = 하위 트리 이동; 자기 자손으로 이동은 400)
DELETE /api/folders/{fid}                             admin, 문서·하위 폴더가 있으면 409 (컬렉션 삭제와 같은 규칙)

# 문서 목록/이동/일괄
GET    /api/documents?collection_id&folder_id&recursive=false&q&status&current_only=true&sort&order&offset&limit
                                                      → {total, items:[DocumentResponse + folder_id, folder_path, lineage_id, version, is_current, sha256]}
POST   /api/documents/bulk                            {ids:[...], action:"move"|"delete"|"reprocess", folder_id?}   admin
                                                      → {done:[ids], failed:[{id, reason}]}
POST   /api/documents                                 (기존) + form field folder_id?, force?   중복이면 409 {existing_document_id, filename, folder_path}

# 버전(현행화)
POST   /api/documents/{id}/versions                   multipart file, effective_date?, note?   admin  202
                                                      → 새 DocumentResponse(version = n+1, is_current = true); 옛 행 is_current=false, superseded_at=now
GET    /api/documents/{id}/versions                   → [{id, version, is_current, filename, size_bytes, effective_date, superseded_at, created_at, uploader_email}]
POST   /api/documents/{id}/versions/{vid}/activate    admin  (되돌리기: 그 버전을 current로, 나머지 false)

# 채팅/검색 범위
POST   /api/chat                                      + folder_ids: [uuid]  (collection_ids와 함께; 폴더는 하위 트리 포함)
GET    /api/tools                                     rag 항목에 folders: [{id, name, path}] 동봉 (멘션 메뉴가 펼친다)
```

DocumentResponse에 추가되는 필드: `folder_id, folder_path(사람이 읽는 "인사/복무" 형태), lineage_id, version, is_current, superseded_at, effective_date, sha256`.

## 단계와 태스크

### 1단계 — 폴더 트리, 이동, 목록 페이지네이션 (배포 가능 단위)

**Task 1.1: 마이그레이션 `0020_folders`.**
```python
op.create_table(
    "folders",
    sa.Column("id", UUID, primary_key=True),
    sa.Column("collection_id", UUID, sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False),
    sa.Column("parent_id", UUID, sa.ForeignKey("folders.id", ondelete="RESTRICT"), nullable=True),
    sa.Column("name", sa.String(200), nullable=False),
    # 조상 id를 '/'로 이어 붙인 경로. 하위 트리 = path LIKE prefix || '%'. ltree 확장 없이.
    sa.Column("path", sa.Text, nullable=False),
    sa.Column("depth", sa.SmallInteger, nullable=False),
    sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.UniqueConstraint("collection_id", "parent_id", "name", name="uq_folders_sibling_name"),
    sa.CheckConstraint("depth BETWEEN 1 AND 8", name="ck_folders_depth"),
)
op.create_index("ix_folders_path", "folders", ["path"], postgresql_ops={"path": "text_pattern_ops"})
op.add_column("documents", sa.Column("folder_id", UUID, sa.ForeignKey("folders.id", ondelete="RESTRICT"), nullable=True))
op.create_index("ix_documents_folder_id", "documents", ["folder_id"])
```
`folder_id NULL` = 컬렉션 루트. 기존 문서는 전부 루트에 남으니 데이터 이관이 없다. 폴더 삭제는 RESTRICT라 문서가 있으면 DB가 거부하고 라우터가 409로 번역한다.

**Task 1.2: 폴더 CRUD 라우터 `app/documents/folders.py`.** 이동(`parent_id` 변경) 시 (a) 대상이 자기 자신·자손이면 400, (b) 깊이 상한 넘으면 400, (c) 하위 트리 `UPDATE folders SET path = replace(path, old_prefix, new_prefix), depth = depth + delta WHERE path LIKE old_prefix || '%'` 한 트랜잭션. 테스트: 자손으로 이동 거부, 형제 이름 충돌 409, 하위 트리 경로 갱신.

**Task 1.3: 문서 목록 재작성.** `GET /api/documents`에 `folder_id/recursive/q/status/sort/order/offset/limit` 추가, `{total, items}` 응답. `q`는 `filename ILIKE` + 등록자 이메일. 기본 정렬 `created_at desc`(현재 동작 유지). `recursive=true`면 `folders.path LIKE prefix%`인 폴더들의 문서 포함.

**Task 1.4: 일괄 엔드포인트 `POST /api/documents/bulk`.** 이동은 같은 컬렉션 안에서만(다르면 그 id를 `failed`에 `reason="컬렉션이 다릅니다"`로). 삭제는 기존 단건 삭제 로직을 루프(파일 삭제 포함). 재처리는 기존 큐 함수 재사용. 50건 상한.

**Task 1.5: 프론트 — 탐색기 화면.** `frontend/app/(app)/documents/page.tsx`를 좌우 2단으로:
- 왼쪽 트리(`components/documents/FolderTree.tsx`): 컬렉션이 루트 노드, 그 아래 폴더. 접기/펼치기, 우클릭·⋯ 메뉴(새 폴더, 이름 바꾸기, 삭제), 현재 폴더 강조. 문서 수 배지.
- 오른쪽 표(`DocumentTable` 확장): 체크박스 열, 헤더 클릭 정렬, 상단 경로 표시줄(빵부스러기, 각 조각 클릭 이동), 선택 시 하단 고정 작업 바("N개 선택 — 이동 · 재처리 · 삭제"). 이동은 폴더 선택 시트(트리 재사용).
- 끌어다 놓기: HTML5 DnD로 표의 행을 트리의 폴더에 놓으면 이동(라이브러리 없이 `draggable`·`onDrop`). 터치 기기는 작업 바의 "이동"이 대체 경로다.
- 모바일(<768px): 트리는 상단 "폴더" 버튼이 여는 바텀 시트(계정 창과 같은 패턴), 표는 카드 목록, 빵부스러기는 가로 스크롤 한 줄. 선택은 길게 누르기 대신 "선택" 토글 버튼으로 체크박스를 켠다(길게 누르기는 iOS에서 텍스트 선택과 충돌).
- 업로드 드롭존은 "현재 폴더에 올리기"가 된다(`folder_id` 동봉).

**Task 1.6: 페이지네이션 UI.** 표 하단 "더 보기"(offset 증가) — 청크 목록의 무한스크롤과 같은 코드. 3초 폴링은 현재 페이지 범위만 다시 읽는다.

### 2단계 — 규정 현행화(버전)와 중복 감지

**Task 2.1: 마이그레이션 `0021_document_versions`.**
```python
op.add_column("documents", sa.Column("lineage_id", UUID, nullable=True))       # 백필: lineage_id = id
op.add_column("documents", sa.Column("version", sa.Integer, nullable=False, server_default="1"))
op.add_column("documents", sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.text("true")))
op.add_column("documents", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
op.add_column("documents", sa.Column("effective_date", sa.Date, nullable=True))
op.add_column("documents", sa.Column("sha256", sa.String(64), nullable=True))
op.execute("UPDATE documents SET lineage_id = id")
op.alter_column("documents", "lineage_id", nullable=False)
op.create_index("uq_documents_lineage_current", "documents", ["lineage_id"], unique=True, postgresql_where=sa.text("is_current"))
op.create_index("ix_documents_sha256", "documents", ["sha256"])
```
`WorkflowVersion`의 부분 유니크 패턴(`models/workflow.py:160-213`) 그대로다. 기존 문서는 각자 계보 1, 버전 1, current.

**Task 2.2: 검색 두 팔과 표 조회에 `is_current` 조건.** `vector_store.py:160-176`과 `keyword_search.py:193-232`의 Document 조인에 `Document.is_current.is_(True)`를 기본으로 넣고, `include_superseded: bool = False` 인자로만 푼다. `table_lookup`의 두 SQL에도 `AND d.is_current`. **이것이 불변식 테스트다**: 옛 버전 청크가 어느 경로로도 안 나오는 것을 `tests/test_versions.py`가 세 경로 모두에서 단언한다.

**Task 2.3: 버전 API.** `POST /documents/{id}/versions`는 업로드 라우터와 같은 저장·큐 흐름을 타되 `lineage_id`를 물려받고 `version = max + 1`, 옛 current를 내린다(`with_for_update`로 계보 잠금 — 프롬프트 라우터 `next_version` 경합 주석과 같은 이유). 새 버전이 `failed`로 끝나면 옛 버전을 다시 current로 올린다(색인 없는 규정이 현행이 되면 안 된다). `activate`는 되돌리기.

**Task 2.4: 중복 감지.** 업로드 스트림에서 SHA-256(이미 파일을 디스크에 쓰므로 같은 패스에서 계산). `sha256`이 같은 current 문서가 있고 `force`가 아니면 409. 새 버전 업로드에서 해시가 이전 버전과 같으면 "내용이 같은 파일입니다"로 409(빈 개정 방지).

**Task 2.5: 프론트 — 문서 상세의 "버전" 카드.** 버전 표(번호·시행일·등록일·크기·현행 표시), "새 버전 올리기" 드롭존(시행일 입력 선택), "이 버전을 현행으로" 버튼, 옛 버전 내려받기. 목록 표에는 현행이 아닌 문서는 기본 숨김 + "이전 버전 보기" 토글, 버전이 2 이상인 문서에 `v3` 배지. 업로드 409는 안내 대화상자("이미 있는 파일 — 그 문서로 가기 / 그래도 올리기").

### 3단계 — 채팅·워크플로우·표 조회의 폴더 범위

**Task 3.1: `folder_ids` 수용.** `schemas/chat.py`에 `folder_ids`, 라우터가 하위 트리 문서 id 집합을 한 번 조회해 `document_ids`로 두 팔에 넘긴다(기존 `collection_ids`와 AND). `workflow.scope_collections` 옆에 `scope_folders`(워크플로우에 폴더 목록이 있으면 교집합, 빈 리스트는 만들지 않음 — `catalogue.py:210-225` 규칙 유지). `WorkflowRagTool`에 `rag_folder_ids`.

**Task 3.2: `@폴더` 멘션.** `GET /api/tools`의 rag 항목에 `folders` 동봉, `mention.ts`가 `rag:{cid}:{fid}` 행으로 펼치고 표시명은 `분류명 / 폴더 / 하위폴더`. 8개 초과면 입력 글자로 필터. 선택 칩은 폴더 경로를 보인다. `ChatWindow`의 `collectionId` 단일 값은 `{collectionId, folderId}`로.

**Task 3.3: 표 조회 범위 주입.** `run_tool_calls`가 builtin 서버 호출 인자에 `document_ids`(요청 범위가 있을 때만)를 덧붙이고, `table_lookup`이 이를 `c.document_id = ANY(:ids)`로 적용. 범위가 없으면 지금처럼 전 코퍼스.

### 4단계 — 관리자 시야(관리 리포트)와 컬렉션 간 이동

**Task 4.1: 폴더 통계.** 트리 노드에 문서 수·총 크기·마지막 갱신일. 컬렉션 관리 화면의 "문서 수" 열을 트리 합계로.

**Task 4.2: "점검 필요" 필터.** 문서 목록에 `stale_days` 필터(예: 시행일 또는 등록일이 N일 이전인 current 규정), 버전이 1개뿐이고 3년 넘은 규정을 노란 배지로. 값은 런타임 설정 `DOCUMENT_STALE_DAYS`(기본 1095)로 고급 설정 "문서 관리" 카테고리에.

**Task 4.3: 컬렉션 간 이동 = 재처리 동반 이동.** `bulk move`에 다른 컬렉션 폴더가 오면 `collection_id`를 바꾸고 재처리를 큐에 넣는다(청킹 전략이 바뀌므로). 화면은 "다른 분류로 옮기면 다시 색인합니다(N건, 약 M분)"를 확인한다.

## 사용자·관리자 관점 점검표 (완료 기준)

- 관리자: 500건이 든 컬렉션을 열어도 첫 화면이 1초 안에 뜬다(목록 50건 페이지). 폴더를 만들고 30건을 골라 끌어 넣는 데 클릭 5회 이내. 개정 규정을 올리면 옛 규정이 검색에서 사라지고, 잘못 올렸으면 한 번에 되돌린다. 같은 파일을 두 번 올리면 즉시 알아챈다.
- 사용자: `@인사/복무`처럼 폴더를 골라 물으면 그 폴더의 현행 문서만 근거가 된다. 폴더를 고르지 않으면 지금과 똑같이 전 코퍼스의 현행 문서를 검색한다. 옛 버전을 근거로 한 답은 나오지 않는다.
- 회귀: 기존 테스트 전부 통과. `collection_ids`만 쓰는 기존 호출은 동작이 바뀌지 않는다(`folder_id NULL` 문서는 루트).

## 위험과 대비

- **is_current 조건 누락.** 검색 경로가 세 곳(dense·sparse·table_lookup)이다. 2.2의 불변식 테스트가 세 곳을 한 픽스처로 묶어 단언한다. 새 검색 경로가 생기면 그 테스트에 추가한다.
- **트리 이동의 경로 갱신 누락.** path는 파생 데이터다. `scripts/check_folder_paths.py`가 parent_id에서 path를 재계산해 대조한다(프롬프트 관리의 `check_*` 스크립트 관례).
- **모바일 끌어다 놓기.** 지원하지 않는다고 못 박고 작업 바의 "이동"이 항상 있게 한다.
- **기존 답변의 인용.** 옛 버전 청크는 지우지 않으므로 과거 답변의 `citations`는 그대로 열린다. 청크 상세 화면에 "이 문서는 v2로 교체됨(현행 보기)" 배너를 단다.

## 순서와 규모

1단계 3~4일, 2단계 2~3일, 3단계 2일, 4단계 2일. 1단계만 배포해도 "폴더로 정리하고 옮기기"는 완성이며, 2단계가 규정 현행화의 핵심이다. 3단계 전까지 채팅은 컬렉션 단위로만 범위를 잡는다(지금과 동일).
