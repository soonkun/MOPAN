/** 코드 탭 - OpenCode 서버 API의 화면 쪽 타입과, 이벤트 스트림을 대화 상태로 접는 순수 함수.
 *
 * 서버(app/code/router.py)는 OpenCode API를 `/api/code/ws/{ws}/oc/{path}`로 그대로 프록시한다. 여기 타입은
 * OpenCode OpenAPI(`/doc`)에서 화면이 실제로 읽는 필드만 옮긴 것이다 - 전체를 베끼지 않는다. */

export interface CodeStatus {
  server_available: boolean;
  server_reason: string;
  bridge: { connected: boolean; has_token: boolean; host?: string | null; version?: string | null; dirs: string[] };
  models: { id: string; label: string }[];
  default_model: string | null;
}

export interface Workspace {
  id: string;
  // server=코드 탭 서버 폴더 · cowork=코워크 작업 폴더(작업마다 하나) · local=내 컴퓨터
  kind: "server" | "local" | "cowork";
  name: string;
  path: string;
  online: boolean;
  host?: string | null;
  /** 코워크 작업만: 파일 수·마지막 수정(epoch 초). 작업 파일 패널의 작업 고르기 목록에 쓴다. */
  files?: number;
  updated_at?: number;
}

export interface OcSession {
  id: string;
  title: string;
  directory?: string;
  time: { created: number; updated: number };
  summary?: { additions?: number; deletions?: number; files?: number };
}

export interface OcToolState {
  status: "pending" | "running" | "completed" | "error";
  input?: Record<string, unknown>;
  output?: string;
  error?: string;
  title?: string;
}

export interface OcPart {
  id: string;
  messageID: string;
  sessionID: string;
  type: string;
  text?: string;
  tool?: string;
  callID?: string;
  state?: OcToolState;
  reason?: string;
  files?: string[];
  tokens?: { total?: number; input?: number; output?: number; reasoning?: number };
}

export interface OcMessageInfo {
  id: string;
  sessionID: string;
  role: "user" | "assistant";
  time: { created: number; completed?: number };
  error?: { name: string; data?: { message?: string } };
  modelID?: string;
  finish?: string;
}

export interface OcMessage {
  info: OcMessageInfo;
  parts: OcPart[];
}

export interface Permission {
  id: string;
  sessionID: string;
  permission: string;
  patterns: string[];
  metadata: Record<string, unknown>;
  always: string[];
}

export interface QuestionItem {
  question?: string;
  header?: string;
  options?: { label: string; description?: string }[];
  multiple?: boolean;
}

export interface Question {
  id: string;
  sessionID: string;
  questions: QuestionItem[];
}

export interface FileDiff {
  path: string;
  status: "added" | "modified" | "deleted";
  additions: number;
  deletions: number;
  patch: string;
}

export interface OcEvent {
  type: string;
  // OpenCode 이벤트마다 모양이 다르다. 접는 함수가 type별로 좁힌다.
  properties: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
}

export interface ThreadState {
  messages: OcMessage[];
  permissions: Permission[];
  questions: Question[];
  busy: boolean;
  error: string | null;
}

export const EMPTY_THREAD: ThreadState = { messages: [], permissions: [], questions: [], busy: false, error: null };

export function ocPath(wsId: string, path: string): string {
  return `/api/code/ws/${encodeURIComponent(wsId)}/oc/${path}`;
}

/** OpenCode의 /event SSE를 POST fetch로 연다. EventSource(GET)가 아닌 이유: Cloudflare quick tunnel이
 * GET 스트림 응답을 연결이 끝날 때까지 통째로 붙잡아 두는 것을 실측했다(2026-09-26, POST 스트림은 즉시
 * 흐른다). 백엔드가 POST를 받아 OpenCode에는 GET으로 올린다. 끊기면 3초 뒤 다시 연다 - EventSource의
 * 자동 재연결을 대신한다. 돌려주는 함수를 부르면 닫힌다. */
export function openOcEvents(wsId: string, onEvent: (evt: OcEvent) => void): () => void {
  const ctrl = new AbortController();
  const run = async () => {
    while (!ctrl.signal.aborted) {
      try {
        const res = await fetch(ocPath(wsId, "event"), {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
          body: "{}",
          signal: ctrl.signal,
        });
        if (!res.ok || !res.body) throw new Error(`event stream ${res.status}`);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const frames = buf.split("\n\n");
          buf = frames.pop() ?? "";
          for (const frame of frames) {
            const line = frame.split("\n").find((l) => l.startsWith("data: "));
            if (!line) continue;
            try {
              onEvent(JSON.parse(line.slice(6)) as OcEvent);
            } catch {
              // 깨진 프레임 하나가 스트림을 죽이지 않게
            }
          }
        }
      } catch {
        if (ctrl.signal.aborted) return;
      }
      await new Promise((r) => setTimeout(r, 3000));
    }
  };
  void run();
  return () => ctrl.abort();
}

function upsertPart(message: OcMessage, part: OcPart): OcMessage {
  const idx = message.parts.findIndex((p) => p.id === part.id);
  const parts = idx < 0 ? [...message.parts, part] : message.parts.map((p, i) => (i === idx ? part : p));
  return { ...message, parts };
}

function withMessage(state: ThreadState, id: string, sessionID: string, fn: (m: OcMessage) => OcMessage): ThreadState {
  const idx = state.messages.findIndex((m) => m.info.id === id);
  if (idx < 0) {
    // 파트가 메시지보다 먼저 도착할 수 있다(스트림 순서 보장 없음). 자리표시자를 두고 info가 오면 채운다.
    const placeholder: OcMessage = {
      info: { id, sessionID, role: "assistant", time: { created: Date.now() } },
      parts: [],
    };
    return { ...state, messages: [...state.messages, fn(placeholder)] };
  }
  return { ...state, messages: state.messages.map((m, i) => (i === idx ? fn(m) : m)) };
}

/** 한 세션의 이벤트 하나를 상태에 접는다. 다른 세션의 이벤트는 그대로 돌려준다(입력과 같은 객체). */
export function applyEvent(state: ThreadState, evt: OcEvent, sessionID: string): ThreadState {
  const p = evt.properties ?? {};
  switch (evt.type) {
    case "message.updated": {
      const info = p.info as OcMessageInfo;
      if (info?.sessionID !== sessionID) return state;
      return withMessage(state, info.id, sessionID, (m) => ({ ...m, info }));
    }
    case "message.part.updated": {
      const part = p.part as OcPart;
      if (part?.sessionID !== sessionID) return state;
      return withMessage(state, part.messageID, sessionID, (m) => upsertPart(m, part));
    }
    case "message.part.delta": {
      if (p.sessionID !== sessionID) return state;
      const field = String(p.field || "text");
      return withMessage(state, p.messageID, sessionID, (m) => {
        const idx = m.parts.findIndex((x) => x.id === p.partID);
        if (idx < 0) {
          const type = field === "text" ? "text" : "reasoning";
          return upsertPart(m, { id: p.partID, messageID: p.messageID, sessionID, type, [field]: String(p.delta) } as OcPart);
        }
        const cur = m.parts[idx] as unknown as Record<string, unknown>;
        const next = { ...cur, [field]: String(cur[field] ?? "") + String(p.delta) } as unknown as OcPart;
        return { ...m, parts: m.parts.map((x, i) => (i === idx ? next : x)) };
      });
    }
    case "permission.asked": {
      if (p.sessionID !== sessionID) return state;
      const perm = p as Permission;
      if (state.permissions.some((x) => x.id === perm.id)) return state;
      return { ...state, permissions: [...state.permissions, perm] };
    }
    case "permission.replied": {
      const id = String(p.requestID ?? p.id ?? "");
      if (!state.permissions.some((x) => x.id === id)) return state;
      return { ...state, permissions: state.permissions.filter((x) => x.id !== id) };
    }
    case "question.asked": {
      if (p.sessionID !== sessionID) return state;
      const q = p as Question;
      if (state.questions.some((x) => x.id === q.id)) return state;
      return { ...state, questions: [...state.questions, q] };
    }
    case "question.replied":
    case "question.rejected": {
      const id = String(p.requestID ?? p.id ?? "");
      if (!state.questions.some((x) => x.id === id)) return state;
      return { ...state, questions: state.questions.filter((x) => x.id !== id) };
    }
    case "session.status": {
      if (p.sessionID !== sessionID) return state;
      const type = p.status?.type as string | undefined;
      return { ...state, busy: type === "busy" || type === "retry" };
    }
    case "session.idle": {
      if (p.sessionID !== sessionID) return state;
      return { ...state, busy: false };
    }
    case "session.error": {
      if (p.sessionID !== sessionID) return state;
      const err = p.error as { name?: string; data?: { message?: string } } | undefined;
      return { ...state, busy: false, error: err?.data?.message || err?.name || "오류가 났습니다." };
    }
    default:
      return state;
  }
}

/** 도구 호출 카드의 한 줄 요약 - 무엇을 어디에 했는지. */
export function toolSummary(part: OcPart): string {
  const input = (part.state?.input ?? {}) as Record<string, unknown>;
  const s = (k: string) => (typeof input[k] === "string" ? (input[k] as string) : "");
  switch (part.tool) {
    case "bash":
      return s("command") || s("description");
    case "read":
    case "edit":
    case "write":
      return s("filePath") || s("path");
    case "glob":
    case "grep":
      return [s("pattern"), s("path")].filter(Boolean).join(" · ");
    case "webfetch":
      return s("url");
    case "list":
      return s("path") || ".";
    default:
      return part.state?.title || Object.values(input).filter((v) => typeof v === "string").join(" ").slice(0, 120);
  }
}

export const TOOL_LABEL: Record<string, string> = {
  bash: "명령 실행",
  read: "파일 읽기",
  edit: "파일 수정",
  write: "파일 쓰기",
  glob: "파일 찾기",
  grep: "내용 검색",
  list: "목록",
  webfetch: "웹 읽기",
  todowrite: "할 일",
  todoread: "할 일",
  task: "하위 작업",
  question: "질문",
};

export const PERMISSION_LABEL: Record<string, string> = {
  bash: "명령을 실행하려고 합니다",
  edit: "파일을 수정하려고 합니다",
  write: "파일을 쓰려고 합니다",
  webfetch: "웹 페이지를 읽으려고 합니다",
  external_directory: "작업 공간 밖 경로에 접근하려고 합니다",
  read: "파일을 읽으려고 합니다",
};
