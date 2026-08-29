// Run: npm test  (node --test --experimental-strip-types lib/api.test.ts)
//
// No test runner, no jsdom, no new dependency: `node --test` and Node's own
// type stripping run this file, and `fetch`/`Response`/`ReadableStream` are all
// globals in Node 22, so a stream can be handed to streamChat directly. Nothing
// here opens a socket - the stub replaces globalThis.fetch, so no test can
// reach a real backend or a real model.
//
// It covers the two failures that were invisible from the outside: a stream
// that stops without a terminal frame, and an aborted one.
import { test } from "node:test";
import assert from "node:assert/strict";

import { ApiError, streamChat } from "./api.ts";
import type { ChatEvent } from "./types.ts";

const TRUNCATED = "답변을 끝까지 받지 못했습니다. 다시 시도해 주세요.";

function sse(...payloads: unknown[]): string {
  return payloads.map((p) => `data: ${JSON.stringify(p)}\n\n`).join("");
}

/** Replaces fetch with one serving `text` as an SSE body. `holdOpen` leaves the
 *  stream unfinished so an abort has something to interrupt; otherwise the body
 *  ends right after `text`, which is exactly what a truncated tail looks like
 *  from the reader's side. Returns the signal fetch was called with. */
function stubFetch(text: string, holdOpen = false): { signal: AbortSignal | null | undefined } {
  const seen: { signal: AbortSignal | null | undefined } = { signal: undefined };
  globalThis.fetch = (async (_input: unknown, init?: RequestInit) => {
    seen.signal = init?.signal;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(text));
        if (!holdOpen) {
          controller.close();
          return;
        }
        init?.signal?.addEventListener("abort", () =>
          controller.error(new DOMException("aborted", "AbortError")),
        );
      },
    });
    return new Response(stream, { status: 200 });
  }) as unknown as typeof fetch;
  return seen;
}

function collect(): { events: ChatEvent[]; onEvent: (e: ChatEvent) => void } {
  const events: ChatEvent[] = [];
  return { events, onEvent: (e) => events.push(e) };
}

const ASK = { conversation_id: null, message: "질문" };

test("a stream that ends without a terminal frame rejects instead of resolving", async () => {
  stubFetch(sse({ type: "status", status: "searching" }, { type: "status", status: "answering" }));
  const { events, onEvent } = collect();

  const err = await streamChat(ASK, onEvent).then(
    () => null,
    (e: unknown) => e,
  );

  // Before the fix this resolved: two status frames delivered, no answer, no
  // error, and the caller cleared its spinner as if the answer had arrived.
  assert.ok(err instanceof ApiError, `expected ApiError, got ${String(err)}`);
  assert.equal(err.message, TRUNCATED);
  assert.equal(events.length, 2);
});

test("a stream ending in done resolves", async () => {
  stubFetch(
    sse(
      { type: "status", status: "answering" },
      { type: "done", conversation_id: "c1", content: "답변", citations: [] },
    ),
  );
  const { events, onEvent } = collect();

  await streamChat(ASK, onEvent);

  assert.deepEqual(
    events.map((e) => e.type),
    ["status", "done"],
  );
});

test("an error frame is terminal too - the caller already showed it", async () => {
  stubFetch(sse({ type: "error", detail: "답변 생성에 실패했습니다." }));
  const { events, onEvent } = collect();

  await streamChat(ASK, onEvent);

  assert.deepEqual(
    events.map((e) => e.type),
    ["error"],
  );
});

test("the signal reaches fetch, and an abort surfaces as AbortError", async () => {
  const seen = stubFetch(sse({ type: "status", status: "searching" }), true);
  const controller = new AbortController();
  const { events, onEvent } = collect();

  const pending = streamChat(ASK, onEvent, controller.signal).then(
    () => null,
    (e: unknown) => e,
  );
  // Let the first frame land before pulling the rug out, so the abort really
  // does interrupt a read in progress.
  await new Promise((resolve) => setTimeout(resolve, 10));
  controller.abort();
  const err = (await pending) as { name?: string; message?: string } | null;

  assert.equal(seen.signal, controller.signal);
  // AbortError, NOT the truncation message: an abort is deliberate, and the
  // caller swallows it rather than putting a red banner on the conversation the
  // user just moved to.
  assert.equal(err?.name, "AbortError");
  assert.notEqual(err?.message, TRUNCATED);
  assert.equal(events.length, 1);
});
