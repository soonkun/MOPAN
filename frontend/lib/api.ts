import type { ChatEvent } from "@/lib/types";

// Empty base URL: every request is same-origin and proxied by next.config.js
// rewrites(). Nothing about the backend location is baked into this bundle.
const API_BASE_URL = "";

// Kept in step with middleware.ts. A 401 on these pages is "wrong password",
// not "your session is gone", and belongs in the error banner.
const PUBLIC_PATHS = ["/login", "/register"];

export class ApiError extends Error {
  // A plain field, not a `public status` constructor parameter property. That
  // shorthand is the one piece of TypeScript here that is not erasable - it
  // emits an assignment - so it was the single line stopping
  // `node --experimental-strip-types` from importing this module, and with it
  // the zero-dependency test in api.test.ts.
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

/** A 401 anywhere but the auth pages means the session is gone - expired in
 * Redis, revoked from another tab, or lost to a server restart - while
 * middleware still saw the cookie and let the page render. Without this the
 * user is left staring at a functional-looking but permanently empty shell
 * with no way back. `replace`, not `href`, so Back does not bounce into it. */
function redirectIfSessionGone(status: number): void {
  if (status !== 401 || typeof window === "undefined") return;
  if (PUBLIC_PATHS.some((p) => window.location.pathname.startsWith(p))) return;
  window.location.replace("/login");
}

const VALIDATION_FALLBACK = "입력한 내용을 다시 확인해 주세요.";
// The fallback for a body with no `detail` at all. It replaces response.statusText,
// which is English and reaches the banner verbatim: an unhandled 500 rendered
// "Internal Server Error", a 404 on a mistyped route renders FastAPI's own
// {"detail":"Not Found"}, and /api/documents' enqueue-failure 503 returned a
// document object with no detail key and rendered "Service Unavailable" while its
// Korean message sat unread in error_message. Backend `detail=` strings are Korean
// by standing constraint (see errorMessage below); this covers every response that
// carries no detail for that constraint to apply to. The status stays in the text
// so a bug report still carries the one fact worth having.
const REQUEST_FAILED = "요청을 처리하지 못했습니다.";
const HANGUL = /[가-힣]/;

/** FastAPI's `detail` is a string for an HTTPException but a LIST for a 422 -
 * the app's own handler returns {"detail": [{loc, msg, type}, ...]}. Reading
 * `.detail` straight into ApiError renders "[object Object]" in the banner,
 * which is exactly the case a user hits by typing a too-long password.
 *
 * Pydantic's own `msg` is English ("Value error, password must be at most 72
 * bytes"), so a raw join swaps [object Object] for English jargon in a Korean
 * UI. Show a msg only when the backend wrote it in Korean; otherwise fall back.
 *
 * The same Hangul test guards the string branch, and not only for symmetry:
 * FastAPI answers an unrouted path with its own `{"detail":"Not Found"}`, which
 * no backend constraint covers because no backend code wrote it. */
function detailText(payload: unknown, fallback: string): string {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return HANGUL.test(detail) ? detail : fallback;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item as { msg?: unknown })?.msg)
      .filter((msg): msg is string => typeof msg === "string" && HANGUL.test(msg));
    if (messages.length > 0) return messages.join(", ");
    return VALIDATION_FALLBACK;
  }
  return fallback;
}

async function failure(response: Response): Promise<ApiError> {
  redirectIfSessionGone(response.status);
  const payload = await response.json().catch(() => null);
  return new ApiError(response.status, detailText(payload, `${REQUEST_FAILED} (HTTP ${response.status})`));
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      // ONLY for string bodies. Setting it for FormData overrides the browser's
      // own multipart boundary and silently breaks every upload.
      ...(typeof options.body === "string" ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    throw await failure(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** Where to land after login. `next` is attacker-supplied via the query string,
 * so only a single-slash relative path is accepted - "//evil.com" and
 * "https://evil.com" are both browser-valid redirect targets otherwise. */
export function safeNextPath(raw: string | null, fallback = "/chat"): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return fallback;
  return raw;
}

/** Only an ApiError carries a message worth showing, and only because every
 *  `detail=` in the backend is written in Korean so that it can be handed
 *  straight to the user. That is a standing constraint on the backend, not an
 *  observation about it: an English `detail=` used to render verbatim on
 *  screen, which is how "conversation not found" once appeared under the Korean
 *  empty state. detailText now drops a detail with no Hangul in it rather than
 *  showing it, so breaking the constraint costs the user the message instead of
 *  showing them English - still a bug, just a quieter one.
 *  A generic Error carries nothing usable at all - a failed fetch throws a
 *  TypeError whose message is the browser's own English string ("Failed to
 *  fetch", "NetworkError when attempting to fetch resource.", "Load failed"),
 *  and returning that verbatim puts English in front of the user. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "알 수 없는 오류가 발생했습니다.";
}

/** A 200 whose body simply stops. `done` and `error` are the only two frames
 * that end a /api/chat stream, so a reader that reaches end-of-body without
 * having seen one was cut off mid-answer - a proxy or tunnel truncating the
 * tail, or the ASGI server killing the generator. Without this the read loop
 * just exited and streamChat resolved normally, so the caller cleared its
 * spinner, re-enabled the form and rendered nothing: the question sat on screen
 * with no answer and nothing to say it had failed. */
const STREAM_TRUNCATED = "답변을 끝까지 받지 못했습니다. 다시 시도해 주세요.";

/** Reads the SSE stream from POST /api/chat. EventSource cannot POST.
 *
 * `signal` aborts the fetch AND the in-flight body read. Not optional in
 * practice: without it a stream outlives the component that started it, and the
 * `done` frame of an abandoned answer still ran that component's
 * `router.replace` - navigating a user who had already clicked away to a URL
 * they never chose. An abort rejects the pending `reader.read()` with an
 * AbortError, so an intentional abort surfaces as that and never as
 * STREAM_TRUNCATED. */
export async function streamChat(
  body: {
    conversation_id?: string | null;
    message: string;
    collection_ids?: string[];
    attachment_ids?: string[];
  },
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/chat`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok || !response.body) {
    throw await failure(response);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminated = false;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        const event = JSON.parse(line.slice("data: ".length)) as ChatEvent;
        if (event.type === "done" || event.type === "error") terminated = true;
        onEvent(event);
      } catch {
        // Ignore a malformed frame rather than killing the whole stream.
      }
    }
  }

  // status 0, the XHR convention for "no HTTP status to report": the response
  // itself was a 200 and only its body failed. ApiError rather than a bare
  // Error because errorMessage() shows the message of nothing else.
  if (!terminated) throw new ApiError(0, STREAM_TRUNCATED);
}
