import type { ChatEvent } from "@/lib/types";

// Empty base URL: every request is same-origin and proxied by next.config.js
// rewrites(). Nothing about the backend location is baked into this bundle.
const API_BASE_URL = "";

// Kept in step with middleware.ts. A 401 on these pages is "wrong password",
// not "your session is gone", and belongs in the error banner.
const PUBLIC_PATHS = ["/login", "/register"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
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
const HANGUL = /[가-힣]/;

/** FastAPI's `detail` is a string for an HTTPException but a LIST for a 422 -
 * the app's own handler returns {"detail": [{loc, msg, type}, ...]}. Reading
 * `.detail` straight into ApiError renders "[object Object]" in the banner,
 * which is exactly the case a user hits by typing a too-long password.
 *
 * Pydantic's own `msg` is English ("Value error, password must be at most 72
 * bytes"), so a raw join swaps [object Object] for English jargon in a Korean
 * UI. Show a msg only when the backend wrote it in Korean; otherwise fall back. */
function detailText(payload: unknown, fallback: string): string {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
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
  return new ApiError(response.status, detailText(payload, response.statusText));
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

/** Only an ApiError carries a message worth showing: it came from the backend
 *  and is already Korean. A generic Error does not - a failed fetch throws a
 *  TypeError whose message is the browser's own English string ("Failed to
 *  fetch", "NetworkError when attempting to fetch resource.", "Load failed"),
 *  and returning that verbatim puts English in front of the user. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "알 수 없는 오류가 발생했습니다.";
}

/** Reads the SSE stream from POST /api/chat. EventSource cannot POST. */
export async function streamChat(
  body: { conversation_id?: string | null; message: string; collection_ids?: string[] },
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/chat`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok || !response.body) {
    throw await failure(response);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

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
        onEvent(JSON.parse(line.slice("data: ".length)) as ChatEvent);
      } catch {
        // Ignore a malformed frame rather than killing the whole stream.
      }
    }
  }
}
