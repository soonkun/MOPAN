# MOPAN — 공용 문서와 개인 문서 (0028)

> 소유자 요구 2026-09-25: "관리자가 등록하는 문서와 사용자가 개인적으로 등록하는 문서를 분리. RAG는 둘 다·한쪽 선택.
> 폴더 감시는 중앙 관리, 파일 업로드는 개인화. 사이드바 관리 안에 '문서 관리', 기존 문서는 '문서 등록'으로 개인화 -
> 다른 사람은 접근 불가."

## 한 문장

**문서에 소유자 열 하나(`documents.owner_id`, NULL = 공용)를 두고, 목록·조회·두 검색 팔이 같은 조건 `owner_visible()`을
쓴다.** 새 표·새 권한 체계·새 검색기는 없다.

## 모델

- `owner_id` nullable FK users(CASCADE), 인덱스. NULL = 공용 코퍼스(관리자 업로드, 감시 폴더). 값 = 그 사용자의 개인 문서.
- `uploaded_by`(NOT NULL, 출처)는 그대로 - 감시 폴더 문서도 올린 사람(시스템 관리자)은 있다.
- 개인 문서는 자동 생성되는 분류 **'개인 문서'** 하나에 들어간다(`documents.collection_id`가 NOT NULL이고 워커 파이프라인이
  분류의 청킹 설정을 읽는다 - 개인 문서는 소유자 열이 격리하므로 분류로 나눌 이유가 없다).
- `owner_visible(user_id, scope)` (app/models/document.py): all=공용+내 것 · shared=공용만 · mine=내 것만. 사용자가 없으면 공용만.

## 경계

| 자리 | 규칙 |
|---|---|
| `POST /api/documents` | 누구나. 일반 사용자는 항상 개인(`scope=mine`), 관리자는 기본 공용(분류 필수), `scope=shared`는 관리자만(403). 중복(sha256) 판정은 같은 소유 범위 안에서만 |
| `get_readable_document(db, id, user)` | 남의 개인 문서는 **404**(존재도 알리지 않음 - 대화와 같은 규칙). 청크 목록·다운로드·삭제가 이 한 함수를 지난다 |
| `GET /api/chunks/{id}` | 이전엔 문서 권한을 안 봤다(인용 클릭용). 개인 문서의 청크는 소유자만 |
| 목록(`/api/documents`, `/api/documents/search?owner=`, 버전 목록) | `owner_visible` 한 줄 |
| 삭제 | 관리자 또는 소유자(`can_manage_document`). 재처리·일괄 이동은 관리자만 그대로 |
| 검색 두 팔 (`PgVectorStore.search`, `keyword_search`) | `Document.is_current` 옆에 `owner_visible(owner_id, doc_scope)`. 기본 `doc_scope="shared"` - 사용자를 모르는 호출(워크플로우 RAG 노드·워커)은 개인 문서를 건드리지 않는다 |
| 딥 리서치 | `ResearchRun.created_by`를 `make_retriever(owner_id=)`로 넘겨 공용 + 실행자의 개인 문서. 보고서는 실행자에게만 가므로 새지 않는다 |
| `/api/chat` | `doc_scope: all|shared|mine`(기본 all). 승인 대기 → 재개 경로(`_pause_frame` 저장분)에도 실린다 |
| 표 조회 MCP(`examples_mcp`) | `AND d.owner_id IS NULL` - 이 프로세스는 누가 묻는지 모른다 |

## 화면

- 사이드바: 일반 메뉴 **문서 등록**(`/my-documents`), 관리 묶음 첫 줄 **문서 관리**(`/documents`). 둘 다 `DocumentsExplorer`에
  `scope`만 다르다. 문서 등록은 트리·빵부스러기 없이 업로드 칸 + 목록, 본인 삭제 가능. 문서 관리는 기존 탐색기 그대로(관리자만 등록).
- 채팅 + 메뉴 "대화 설정"에 **문서 범위**(공용 + 내 문서 → 공용 문서만 → 내 문서만). 기본이 아닐 때만 칩.

## 검증

- 테스트 202건 통과(문서 API·스키마·폴더·검색·버전·감시·채팅). 새 테스트: 일반 사용자 업로드 → 개인('개인 문서' 분류), 본인 200·관리자 404,
  목록에 없음, 본인 삭제 204 · 일반 사용자 `scope=shared` → 403.
- 실서버: probe-b가 올린 mine.txt가 probe-a의 `owner=all` 검색에 0건, 본인 `owner=mine`에 1건. `/api/chat`이 `doc_scope` 수용.

## 남긴 것

- 워크플로우 RAG 노드에서 개인 문서: 노드에 사용자가 없어 공용만. 필요해지면 실행 컨텍스트의 user_id를 RagTool로 넘기는 한 줄.
- 그룹 공유(부서 문서): nullable owner로는 표현 못 한다. 그때 ACL 표.
