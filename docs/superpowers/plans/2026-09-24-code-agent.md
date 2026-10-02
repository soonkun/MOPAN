# MOPAN — 코드(코딩 에이전트) — 조사·기획·구현 기록

> 소유자 요구 2026-09-24: "클로드 코드처럼 코딩 에이전트를 MOPAN에 넣자. 요즘 가장 좋다는 오픈 코딩
> 에이전트를 붙이는 방식으로. 사용자가 자기 컴퓨터의 폴더를 지정해 그 안을 에이전트가 읽고 쓸 수 있게.
> 코딩 모델은 나중에 정한다."
> 코드: `backend/app/code/`, `frontend/app/(app)/code/`, `frontend/components/code/`, 컴패니언 `backend/app/code/companion/mopan-code.mjs`.

## 한 문장

**에이전트 루프를 만들지 않는다.** OpenCode의 headless 서버(`opencode serve`, HTTP+SSE, OpenAPI 3.1)를
런타임으로 쓰고, MOPAN은 세 가지만 맡는다 — **누가**(세션 쿠키·토큰), **어디서**(사용자별 샌드박스 또는
사용자 PC), **어떤 모델로**(카탈로그 허용 목록을 지키는 OpenAI 호환 프록시).

## 1. 조사 (2026-09-24)

### 1.1 Claude의 세 표면과 "웹에서 코드"

| 표면 | 실행 위치 | 로컬 폴더 |
|---|---|---|
| Claude Code on the web (claude.ai/code, 2025-10 출시) | Anthropic 관리 격리 VM. GitHub 레포를 clone, 또는 번들 업로드(100MB) | **없음** |
| Claude Code **Remote Control** (`claude remote-control`) | **사용자 PC의 CLI가 계속 실행**, 웹/모바일은 창(window). 아웃바운드 HTTPS만, 인바운드 포트 없음 | 있음 — 로컬 CLI가 파일·셸을 다룬다 |
| Cowork | 클라우드 실행. 로컬 폴더는 **Desktop 앱이 켜져 있을 때** 그 앱이 브리지 | 있음(Desktop 브리지 경유) |
| Teleport(`claude --teleport`) | 클라우드 세션의 브랜치·대화를 로컬 CLI로 가져옴(단방향) | — |

즉 "웹에서 코드"는 있지만 로컬 폴더는 **PC에서 도는 프로세스가 아웃바운드 연결로 붙는 패턴**(Remote
Control, Cowork Desktop 브리지, OpenAI Codex Remote도 같음)으로만 도달한다. 이 슬라이스의 "내 컴퓨터
연결"은 그 패턴이다.

### 1.2 오픈 코딩 에이전트 (2026-09 기준, GitHub 별 수는 조사 당일 값)

| 에이전트 | 라이선스/스택 | ★ | 임베딩 모드 | 커스텀 OpenAI 호환 엔드포인트 |
|---|---|---|---|---|
| **OpenCode** (anomalyco) | MIT / TS(Bun) | 209.8k | `opencode serve`(REST+SSE, OpenAPI, basic auth, 권한 응답 API), TS SDK, `opencode web`, ACP | `@ai-sdk/openai-compatible` + baseURL |
| DeepSeek Harness | MIT / TS | ~235k | 웹 UI·서버 | 있음(2차) — **developer preview, breaking change 예고** |
| Codex CLI (OpenAI) | Apache-2.0 / Rust | 126k | `app-server`(JSON-RPC), SDK, remote-control | `wire_api="responses"`만 — vLLM Responses API 필요 |
| Pi (badlogic) | MIT / TS | 109k | `--mode rpc`, SDK | 있음. **권한 시스템 없음** |
| Gemini CLI | Apache-2.0 / TS | 107k | ACP | **Gemini API 전용**(이슈 #23385 미해결) |
| Cline | Apache-2.0 / TS | 69k | headless CLI, Node SDK | 있음 |
| Goose (Linux Foundation) | Apache-2.0 / Rust | 55k | goosed(REST/WS), ACP | 있음 |
| Qwen Code | Apache-2.0 / TS | 28k | `qwen serve`(HTTP+SSE, 실험) | 있음(vLLM 명시) |
| Crush | FSL-1.1-MIT / Go | 28k | `crush serve` | 있음 |
| Kilo Code | MIT | 27k | **OpenCode 포크** | 게이트웨이 |
| Aider / Continue / Roo Code | — | — | 순수 CLI · Cursor 인수(read-only) · 2026-05 archived | 채택 대상 아님 |

ACP(Agent Client Protocol, Zed): 에디터↔로컬 에이전트의 stdio JSON-RPC. 원격 전송은 WIP라 서버측
웹앱이 직접 쓰기엔 이르다. OpenCode·Codex·Gemini·Goose·Qwen 모두 구현.

**선택: OpenCode.** 근거 넷 — (1) MIT에 가장 큰 생태계(Kilo가 이 위에 있다), (2) 언어 무관 HTTP+SSE
서버라 FastAPI는 프록시만 하면 되고 권한 요청도 HTTP 응답 API다, (3) 한 프로세스가 `?directory=`로
여러 디렉터리를 섬긴다(실측 162 경로의 OpenAPI), (4) 카탈로그의 어떤 모델이든 OpenAI 호환으로 붙는다.
대안 순서: Codex app-server(Responses API 필요) → Pi(권한 UI 자작) → Cline.

### 1.3 "내 컴퓨터 폴더"를 웹앱이 다루는 네 방식

| | 방식 | 판정 |
|---|---|---|
| a | 브라우저 File System Access API(`showDirectoryPicker`) | Chromium만, **셸 실행 불가** → 테스트·git을 못 돌리는 코딩 에이전트는 반쪽 |
| b | **PC의 컴패니언이 아웃바운드로 붙는 역터널**(Claude RC, Codex Remote, Cowork Desktop) | **채택.** 에이전트는 PC에서, MOPAN은 화면·모델 |
| c | 사용자가 OpenCode를 직접 깔고 MOPAN을 모델 프로바이더로만 | UI 통합 없음. 단, b의 컴패니언은 사실상 c + 터널이다 |
| d | 서버 샌드박스 워크스페이스(Claude Code 웹, Codex cloud) | **채택(기본).** 로컬 접근 문제 자체가 없고 격리·자원 통제가 쉽다 |

### 1.4 이 호스트에서 실측한 것 (전부 이 문서의 결정에 들어갔다)

- `opencode serve`는 `?directory=`마다 인스턴스를 띄우고 `/event`도 디렉터리별이다. `/global/health`,
  `/doc`(OpenAPI), `/session`, `/session/{id}/message`(동기) · `/prompt_async`, `/permission`,
  `/permission/{id}/reply` `{reply: once|always|reject}`, `/session/{id}/diff`.
- 내장 웹 UI(`opencode web`)는 `/assets/*` 절대 경로와 `location.origin`을 쓴다 → 경로 접두어 뒤에
  못 놓는다. MOPAN 화면은 **자체 UI**다(무겁지 않다 — 이벤트 12종만 그린다).
- **샌드박스**: 비특권 user namespace는 AppArmor가 막고, `--unshare-user`+`--proc`는 함께 안 된다.
  되는 조합은 *root bwrap이 마운트·pid/ipc/uts 네임스페이스를 만들고 `setpriv`가 실제 uid로 내리는 것*.
  안에서 uid=60001, caps 0, /etc/shadow EACCES, 자기 홈·작업 공간만 쓰기 가능. 중간 디렉터리는
  `--perms 0755 --dir`로 먼저 만들어야 한다(bwrap 기본 700). `--clearenv` 필수(백엔드 비밀이 새지 않게).
- **바인드 원본 경로는 uid가 지나갈 수 있어야 한다**: `/NHNHOME/.../SAS`가 `drwxr-s---`라 저장소 안의
  data/는 못 쓴다 → `/var/lib/mopan-code/{bin,node20,users}`.
- **vLLM 유휴 재우기가 직접 호출을 죽인다**: 잠든 8001에 opencode가 바로 보낸 요청은 "SSE read timed
  out" 재시도만 반복했다. 깨우는 것은 MOPAN 프로세스의 일(`ensure_awake`) → 모델은 반드시 MOPAN 프록시
  경유. 깨운 뒤 같은 요청은 **6초**에 끝났다(읽기 → 편집 → bash 권한 요청 → 승인 → 실행 → 답).
- gemma4:26b(vLLM, max_model_len 16384)는 OpenCode 시스템 프롬프트+도구만 **7,261 토큰**을 쓴다.
  16k 창은 코딩 에이전트에 빡빡하다 — 모델을 정할 때 컨텍스트 창을 먼저 본다(`CODE_MODEL_CONTEXT`).
- **limit.output은 limit.context보다 작아야 한다**: 16384 창에 output 32768을 알리자 남은 창이 음수라 OpenCode가 매
  단계 자동 압축(compaction) → "계속" 합성 메시지 → 압축 … 을 1,572메시지까지 반복했다(gemma4:26b). `model_limit()`이
  output=context/4로 묶고 컴패니언도 서버가 준 값을 쓴다. 같은 이유로 `"*": allow`가 덮어 버리는 `doom_loop`는 ask로 되살렸다.
- Next.js rewrites 프록시는 헤더가 30초 안에 안 오면 500을 낸다 → 화면은 동기 `/message` 대신 `prompt_async`(204) + 이벤트.
- Next.js rewrites는 WebSocket을 넘기지 않는다(공개 주소는 cloudflared → Next → 백엔드) → 컴패니언
  터널은 **SSE(내려감) + POST(올라옴)** 두 HTTP 길로만 만든다(이미 채팅 SSE가 그 길을 검증했다).

## 2. 설계

```
브라우저 ──세션 쿠키──▶ /api/code/ws/{ws_id}/oc/{path}  (OpenCode API 그대로 프록시, SSE 포함)
                            │  srv:<이름>                      loc:<b64 경로>
                            ▼                                   ▼
             bwrap+setpriv 샌드박스(uid 6xxxx)          Bridge(사용자별 1연결)
             opencode serve --port N (basic auth)        ▲ SSE 요청 프레임 │ POST 응답 프레임
             /workspace/<이름>  ·  /home/code(세션 DB)   컴패니언 mopan-code.mjs (사용자 PC)
                            │                             └▶ 로컬 opencode serve
                            └──────── Bearer 토큰 ──────────▶ /api/code/llm/v1/chat/completions
                                                              (카탈로그 허용 목록 · ensure_awake · vLLM/Ollama/OpenAI로 그대로 스트리밍)
```

**결정들**
- 사용자마다 프로세스, 사용자마다 uid(`code_profiles.sandbox_uid`, 60000+). 파일 소유권이 격리다.
  프로세스는 첫 요청에 뜨고 `CODE_IDLE_MINUTES` 쉬면 회수, 상한(`CODE_MAX_PROCESSES`) 넘으면 가장
  오래 쉰 것부터. 세션 이력은 OpenCode가 사용자 홈 sqlite에 남기므로 재시작해도 이어진다.
- **DB 표는 하나**(`code_profiles`): 작업 공간은 디스크의 디렉터리 목록이 곧 목록이고, 세션은
  OpenCode의 것이다. 두 번 저장하지 않는다.
- 권한 기본값은 Claude Code의 결: 읽기 자유, **edit·bash·webfetch는 물어봄**, 작업 공간 밖은 거절.
  화면에서 "이번만/항상/거부", 그리고 "자동 승인" 토글(화면이 대신 once로 답한다).
- 컴패니언은 **의존성 0의 파일 하나**(Node 22+, opencode 설치 필요). 인바운드 포트 없음. 토큰은 계정에
  묶이고 해시로만 저장, 재발급하면 이전 것은 즉시 죽는다. 조종은 같은 계정의 세션 쿠키만.
- 모델은 카탈로그(고급 설정 → 모델)가 곧 목록이다. 코드용 별도 목록·설정은 만들지 않았다 — 코딩
  모델이 정해지면 카탈로그에 켜고 기본으로 두면 끝.

## 3. API

| 경로 | 뜻 |
|---|---|
| `GET /api/code/status` | 서버 샌드박스 가능 여부·사유, 내 컴퓨터 연결 상태, 모델 목록 |
| `GET/POST /api/code/workspaces`, `DELETE /api/code/workspaces/{id}` | 서버 작업 공간 목록(+연결된 PC 폴더)·생성·삭제 |
| `POST /api/code/workspaces/{id}/files` | 파일 여러 개(`paths`로 상대 경로) 또는 zip 하나 |
| `GET /api/code/workspaces/{id}/download` | zip(node_modules·.venv·.git 제외) |
| `POST /api/code/bridge/token` | 컴패니언 토큰 발급(원문은 이때 한 번) |
| `GET /api/code/companion` | 컴패니언 스크립트 내려받기 |
| `GET /api/code/bridge/stream` · `POST /api/code/bridge/reply/{id}` | 컴패니언 전용(Bearer) |
| `ANY /api/code/ws/{id}/oc/{path}` | OpenCode API 프록시. `directory`는 서버가 채운다 |
| `POST /api/code/llm/v1/chat/completions` · `GET .../models` | opencode 전용 모델 프록시(Bearer) |

## 3.5 화면의 약속 (2026-09-25, 소유자 휴대폰 화면 지적 후)

1. **들어오면 바로 친다** - 작업 공간이 없으면 '내 작업'을 자동으로 만들고, 입력창은 작업 공간이 있는 한 항상 떠 있다.
   빈 대화는 "무엇을 만들까요?" + 예시 칩 4개.
2. **무엇을 하는지 보인다** - 도구 카드가 순서대로, 지금 도는 도구는 입력창 옆 상태 줄에("실행 중 · 명령 실행 · python3 a.py").
3. **결과물을 손에 쥔다** - 답 밑 '변경 파일 N개'(파일마다 받기, 전체 zip) + 오른쪽 '파일' 패널(탐색·미리 보기·받기,
   `GET /api/code/workspaces/{id}/file?path=`). 첨부(📎)는 작업 공간에 올리고 프롬프트에 파일명을 적어 준다.
4. 모바일은 머리 두 줄(제목 줄 / 모델·자동 승인 줄), 작업 공간·파일은 전체 화면 시트. Enter 전송은 데스크톱만.
5. 코드 탭 기본 모델은 `CODE_DEFAULT_MODEL`(qwen3.8:27b) - 답변 모델(gemma4)과 다르다. 고른 모델은 브라우저가 기억.

## 3.6 코워크 · 코드 두 입구 (2026-09-25)

머리의 간편/개발 토글은 하루 살고 사라졌다(소유자: "처음 들어가는 경로를 완전히 다르게"). 사이드바에 **코워크**(`/cowork`)와
**코드**(`/code`) 두 메뉴. 같은 컴포넌트(`components/code/CodeAgentPage.tsx`)에 `mode`만 다르다. 서버 작업 공간은 둘이 공유한다 -
코워크에서 만든 파일을 코드에서 다듬을 수 있다.

| | 코워크 | 코드 |
|---|---|---|
| 누가 | 설치 없이 자료를 넣고 결과 파일을 받는 사람 | 코딩하는 사람 |
| 작업 공간 | 서버만 | 서버 + 내 컴퓨터 연결 |
| 권한 | 자동 승인 켜짐(샌드박스 안) | edit·bash·webfetch 매번 확인 |
| 과정 | "작업 과정 N단계"로 접힘 | 도구 카드 전부 |
| 입력창 칩 | 자동 승인 칩(모델 칩은 + 메뉴에만) | 모델 칩 |
| 예시 칩 | 엑셀 합치기·CSV 요약·회의록 docx·PDF 표 추출 | 코드 구조 설명·테스트 고치기·스크립트·README |
| 기억 | `cowork.ws` | `code.ws` |

Anthropic의 Cowork·Claude Code 데스크톱과 같은 역할 분담이지만 **연동은 없다** - 전부 이 서버의 OpenCode와 로컬 모델(§3.7).
폴더 첨부는 없다(폴더 단위면 그 폴더가 작업 공간 - 코드의 내 컴퓨터 연결).

## 3.6.1 코워크는 작업마다 폴더 하나 (2026-09-25)

소유자 셋: 결과물은 '작업 파일'에서 받는다(넣은 것·만든 것 함께) · 30일 지나면 지운다 · 지난 작업을 히스토리에서 불러온다.
파일 단위 보존 기한은 진행 중 작업의 오래된 입력만 골라 지워 작업을 깨뜨리므로 **작업(대화) = 폴더**로 바꿨다.

- 경로 `users/<uid>/cowork/<작업 이름>`, 샌드박스 안 `/cowork/<이름>`, ws id `cw:<이름>`. 이름은 첫 요청 앞 40자(서버가 다듬고 중복엔 번호).
- 화면: 들어오면 초안(폴더 없음). 첫 요청·첫 첨부가 폴더를 만든다. 왼쪽 '지난 작업' = 폴더 목록(최근 수정 순), 고르면 그 작업의
  마지막 대화를 바로 편다. 머리에 '작업 파일'(FilesPanel) - 코드 탭은 서버 작업 공간에만 '파일', 내 컴퓨터 폴더에는 없음.
- 보존: `CODE_COWORK_RETENTION_DAYS=30`. 감시 워커 크론(매일 18:30 UTC=03:30 KST)이 폴더 안 최근 수정이 기한을 넘은 작업 폴더를
  통째로 지운다(`router.purge_cowork_tasks`). OpenCode 세션 행은 sqlite에 남지만 폴더가 없어 목록에 안 뜬다.
- API: `GET/POST /api/code/cowork/tasks`. 삭제·업로드·zip·파일 받기는 기존 `/api/code/workspaces/{cw:..}` 그대로.

## 3.7 데이터가 밖으로 나가지 않는다 (2026-09-25)

소유자 원칙: 코드 탭은 로컬 모델만. `CODE_LOCAL_ONLY=true`(기본)면 코드 탭의 모델 목록·기본 모델·LLM 프록시 전부
provider=local(이 서버의 vLLM·Ollama)만 통과한다 - 카탈로그에 GPT가 켜져 있어도 코드 탭에서는 보이지도 받지도 않는다.
채팅의 카탈로그는 건드리지 않는다. Cowork·Claude Code 데스크톱은 이 문서에서 **비교 대상**일 뿐, MOPAN은 Anthropic·OpenAI를
코드 탭에서 부르지 않는다. 남은 바깥 길은 에이전트의 webfetch·bash(curl) - 개발 모드는 매번 묻고, 간편 모드는 자동 승인이므로
외부 접속을 완전히 끊어야 하면 opencode 설정에 `tools.webfetch=false`와 네트워크 네임스페이스 분리가 다음 단계.

## 4. 남긴 것 (ponytail)

- 파일 트리·에디터 뷰 없음 — 변경은 세션 diff로 본다. 필요해지면 `/file`·`/file/content` 프록시는 이미 열려 있다.
- 사용량·비용 집계 없음 — 프록시가 지나는 자리라 붙이기는 한 곳이다.
- 컴패니언 단일 실행 파일(exe) 없음 — Node 22 설치를 요구한다. 요청이 오면 `bun build --compile`.
- `/var/lib/mopan-code`는 컨테이너 루트 오버레이다 - 컨테이너가 다시 만들어지면 사라진다(/NHNHOME은 남는다). 그때는
  `opt/opencode/node_modules/opencode-linux-x64/bin/opencode`와 `opt/node20`을 다시 복사하고 users/는 비어서 시작한다.
- opencode 버전 고정: 1.18.32(`/var/lib/mopan-code/bin/opencode`). 올릴 때 `/doc` 스키마 변화를 본다.


## §5 내 컴퓨터 연결이 터널 뒤에서 500 (2026-09-26)

증상: 컴패니언은 "MOPAN에 연결됨"인데 `내 컴퓨터` 작업 공간의 첫 요청(`/oc/session`)이 30초 뒤 500(Next 프록시 시간 초과). 백엔드 로그에 `bridge/reply`가 한 건도 없다.
원인: **Cloudflare quick tunnel이 GET 스트림(text/event-stream) 응답을 헤더까지 연결이 끝날 때까지 통째로 붙잡아 둔다.** 브리지 스트림(컴패니언이 GET으로 열던 SSE)에 실린 요청 프레임이 PC에 닿지 않았다. 같은 이유로 브라우저 EventSource(`/oc/event`)도 터널 뒤에서는 죽어 있었다. 실측: 터널→Next, 터널→백엔드 직결, http2/quic, Accept-Encoding, HTTP/1.1, 8KB 패딩 전부 같음. POST `/api/chat` SSE는 즉시 흐름. 서버에서 127.0.0.1로 붙으면 GET도 정상이라 로컬 테스트로는 절대 안 잡힌다.
수정: 오래 사는 스트림을 전부 POST로. `/api/code/bridge/stream`이 POST를 받고(컴패니언 `mopan-code.mjs`가 POST로 연다 - **사용자는 컴패니언 파일을 다시 받아야 한다**), `/oc/event`는 브라우저가 POST로 열면 백엔드가 OpenCode에 GET으로 올린다(`lib/code.ts openOcEvents`, 3초 재연결). 검증: 서버에서 컴패니언을 공개 터널 주소로 붙이고 터널 경유 `GET /oc/session` 200(4.7초), `POST /oc/event` server.connected 즉시·heartbeat 10초.
덤: 새 계정 첫 방문의 `get_profile` uid 경합(MissingGreenlet 500)도 같은 날 수정.


## §6 내 컴퓨터 연결을 한 줄로 (2026-09-26)

사용자 지적: "컴패니언 파일이 어느 폴더에 있어야 하는지 설명이 없다. 폴더만 알려주면 내려받기·토큰·실행을 자동으로 할 수 있지 않나. Node.js·OpenCode 설치만 안내하고."
브라우저는 PC 프로그램을 실행하지 못하므로 '연결' 버튼 하나로는 안 된다(Claude Desktop은 네이티브 앱이라 가능). 그 다음으로 짧은 길:
- `ConnectDialog`: OS 탭(자동 감지) → 폴더 경로 입력 → "연결 명령 만들기"(토큰 발급) → 한 줄 명령 + 복사 버튼. 직접 하는 방법은 맨 아래 한 줄로.
- 한 줄 명령(Windows): `$env:MOPAN_SERVER='…'; $env:MOPAN_TOKEN='…'; $env:MOPAN_DIR='C:\…'; irm …/api/code/setup.ps1 | iex` / (macOS·Linux): `MOPAN_SERVER=… MOPAN_TOKEN=… MOPAN_DIR=… bash -c "$(curl -fsSL …/api/code/setup.sh)"`.
- `setup.ps1`/`setup.sh`(공개, 비밀 없음): Node 22+ 확인(없으면 nodejs.org 열고 종료 → 설치 후 같은 명령 다시) → OpenCode 없으면 `npm i -g opencode-ai`(bash는 권한 실패 시 ~/.mopan/npm에 설치해 `--opencode`로 넘김) → 컴패니언을 `%LOCALAPPDATA%\MOPAN`(`~/.mopan`)에 Bearer 토큰으로 내려받기(`/api/code/companion`이 쿠키 또는 브리지 토큰을 받게 바꿈) → 다음부터 두 번 클릭할 `connect.cmd`(+`connect.ps1`, 한글 경로 때문에 내용은 ps1 UTF-8 BOM)/`connect.sh` 생성 → 실행.
- 검증(Linux, 빈 HOME): 컴패니언 내려받기·connect.sh 생성·연결·`/oc/session` 200. PowerShell은 이 서버에 없어 실행 검증은 못 했다 - 사용자의 첫 실행 결과를 본다.
다음 단계 후보: `mopan-code://` URL 스킴을 setup이 등록하면 이후에는 화면의 '연결' 버튼이 바로 PC의 컴패니언을 띄울 수 있다(설치 한 번은 여전히 필요).

- 가독성(같은 날): 대화 열이 프롬프트 창보다 넓었다(대화 max-w-4xl 896 vs 프롬프트 max-w-4xl 안의 px-6 → 848). 둘 다 `max-w-3xl`(768)로 맞추고, 프롬프트 창의 여백은 바깥 div로 옮겨 안쪽 폭이 같게. 좁힌 만큼 파일 패널을 `md:w-80`(320) → `md:w-[26rem]`(416)·`xl:w-[34rem]`(544)로.
- 파일 패널 미리 보기 높이 조절(같은 날): 목록과 미리 보기 사이 구분선(role=separator)을 끌어 높이를 바꾼다. 최소 120px, 목록 최소 160px. 키보드 위/아래 24px, 두 번 클릭이면 기본 45%. localStorage `code.files.previewH`에 기억. Playwright 실측: 405→649px 끌기, 새로 고침 뒤 유지, ArrowDown −24, 더블클릭 복원.
- 덤으로 잡은 버그: 서버 작업 공간의 1KB 넘는 파일 미리 보기가 "알 수 없는 오류" - httpx 기본 accept-encoding: gzip으로 OpenCode가 압축한 바이트를 content-encoding 없이 흘렸다. 프록시가 `Accept-Encoding: identity`로 요청.


## §7 답변 전 깜빡임·새 계정 500·샌드박스 웹 접근 (2026-09-27)

- **깜빡임(채팅·코워크 공통, 첫 질문 때만)**: 채팅은 첫 답변 뒤 `router.replace('/chat/{id}')`가 /chat → /chat/[id] 라우트 전환이라 ChatWindow가 통째로 갈리며 화면이 한 번 비었다(Playwright: 빈 프레임 6, textarea 교체). `window.history.replaceState`로 주소만 바꾸고, Back/Forward로 /chat 페이지가 대화 없이 뜨는 경우는 "주소의 id를 연다" 효과로 막았다(Next 15.5 native replaceState 통합 → usePathname 갱신 → 사이드바 목록도 갱신). 실측: 빈 프레임 0, textarea 유지, 사이드바에 새 제목, 다른 대화 → Back 복원 OK. 코워크는 새 작업 때 `openSession`이 `EMPTY_THREAD`로 비우고 다시 채우던 것을, 같은 세션이면 비우지 않고 send가 사용자 말풍선을 먼저 그려 두게 바꿈.
- **새 계정 첫 /api/code/status 500(두 번째)**: INSERT에 안 실린 열(bridge_token_hash, created_at)이 만료 상태 → `profile.bridge_token_hash` 접근이 lazy load → MissingGreenlet. `db.refresh(profile)` 한 줄. 재현: 프로필 삭제 후 /status 단독 200, /cowork/tasks 후 /status 200.
- **"opencode가 안 된다"**: 샌드박스는 방화벽으로 인터넷이 없는데 webfetch가 ask라 코워크(자동 승인)가 시도하고 실패만 남겼다. `webfetch: deny` + 전역 규칙 `~/.config/opencode/AGENTS.md`("인터넷 없음, 파일·명령은 됨, 한국어") 생성.
