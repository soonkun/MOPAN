"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, errorMessage, streamChat } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import MessageBubble from "@/components/chat/MessageBubble";
import type { Message } from "@/lib/types";

const STATUS_LABEL: Record<string, string> = {
  searching: "문서 검색 중…",
  answering: "답변 생성 중…",
};

export default function ChatWindow({
  initialConversationId,
}: {
  initialConversationId: string | null;
}) {
  const router = useRouter();
  const [conversationId, setConversationId] = useState<string | null>(initialConversationId);
  const [messages, setMessages] = useState<Message[]>([]);
  // "no messages", "not loaded yet" and "the load failed" are three states, and
  // the empty-state line below belongs to only the first - the same distinction
  // the Sidebar draws for its conversation list, its `!error &&` included.
  // Without `loaded`, every arrival at /chat/{id} flashes 등록된 문서에 대해
  // 무엇이든 물어보세요. before the transcript lands: measured at 40ms over
  // loopback, and it is a network round trip, so it is only ever longer in
  // front of a real user. Without `!error`, a failed load shows that same
  // invitation stacked on top of the error banner, because setLoaded runs in
  // finally() and a rejected fetch is therefore loaded-and-empty.
  const [loaded, setLoaded] = useState(!initialConversationId);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The answer, repeated into an off-screen live region - see the markup below
  // for why the transcript itself cannot be the live region.
  const [announcement, setAnnouncement] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Abort an answer still in flight when this window stops being the one on
  // screen. Without it streamChat outlived the component and its closure kept
  // the old `router`: ask at /chat, click another conversation mid-answer, and
  // ~3.5s later the abandoned stream's `done` frame ran router.replace and
  // threw the browser onto a conversation the user never chose.
  //
  // Keyed on initialConversationId, not []: /chat/{a} -> /chat/{b} is the same
  // component in the same slot, so React re-renders it with a new prop rather
  // than unmounting it, and a []-keyed cleanup never runs for the case that
  // actually reproduced.
  useEffect(() => () => abortRef.current?.abort(), [initialConversationId]);

  useEffect(() => {
    if (!initialConversationId) return;
    apiFetch<Message[]>(`/api/conversations/${initialConversationId}/messages`)
      .then(setMessages)
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoaded(true));
  }, [initialConversationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, status]);

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || sending) return;

    const question = input;
    const pendingId = `temp-${Date.now()}`;
    const controller = new AbortController();
    abortRef.current = controller;
    setInput("");
    setError(null);
    setAnnouncement("");
    setSending(true);
    setMessages((prev) => [
      ...prev,
      {
        id: pendingId,
        role: "user",
        content: question,
        citations: [],
        created_at: new Date().toISOString(),
      },
    ]);

    try {
      let newConversationId: string | null = null;
      // Neither `token` nor `citations` gets a branch, both deliberately: Slice 1's
      // answer() is a single non-streaming llm_provider.chat() call so `token` is
      // never emitted at all (it is Slice 3's), and the `citations` frame carries
      // the identical array that `done` carries one frame later.
      await streamChat(
        { conversation_id: conversationId, message: question },
        (event) => {
          if (event.type === "status") {
            setStatus(STATUS_LABEL[event.status] ?? null);
          } else if (event.type === "error") {
            setError(event.detail);
            // Take the question back off screen with it. An `error` frame means
            // the backend rolled the conversation back - a brand new one is
            // deleted, an existing one keeps neither message - so leaving the
            // bubble up shows a question that is not saved anywhere, and the
            // next reload silently loses it. Only this branch does it: a
            // truncated stream or a dropped connection throws instead, and
            // there the backend may well have committed the exchange.
            setMessages((prev) => prev.filter((m) => m.id !== pendingId));
          } else if (event.type === "done") {
            newConversationId = event.conversation_id;
            setAnnouncement(event.content);
            setMessages((prev) => [
              ...prev,
              {
                id: `assistant-${Date.now()}`,
                role: "assistant",
                content: event.content,
                citations: event.citations,
                created_at: new Date().toISOString(),
              },
            ]);
          }
        },
        controller.signal,
      );

      if (!conversationId && newConversationId) {
        setConversationId(newConversationId);
        // router.replace, and NOT window.history.replaceState. Both were
        // measured on `next start`, same clicks, only this line differing, with
        // POST /api/chat answered by a stubbed SSE body naming an existing
        // conversation.
        //
        // router.replace costs a full document load here (performance.timeOrigin
        // changes; /api/auth/me and /api/conversations are requested again), so
        // the answer that just rendered is off screen until the new page's
        // transcript fetch lands: rAF frames of the new document at 31, 53 and
        // 63ms hold no messages and the transcript is back at 80ms, ~76ms end to
        // end over loopback. Everything downstream is then correct - Back
        // re-requests /api/conversations/{id}/messages and restores the
        // conversation, Forward returns to the one clicked in the sidebar, and
        // reload matches both.
        //
        // window.history.replaceState removes that reload, and the Sidebar still
        // refetches because usePathname() still changes. It also corrupts the
        // history entry, which is worse. Next patches replaceState to re-run its
        // router restore with the tree it already has, so the entry keeps the
        // /chat (new-chat) tree while its URL becomes /chat/{id}. Measured: the
        // next sidebar click degrades to a full page load, and Back then restores
        // that entry making NO request at all - the transcript it showed was the
        // two messages left in memory where the conversation has four, and
        // nothing ever refetches it. 76ms of flicker is cosmetic; a history entry
        // whose page disagrees with its URL is not.
        router.replace(`/chat/${newConversationId}`);
      }
    } catch (err) {
      // An abort is this component's own doing, not a failure: the user moved
      // on. Rendering it would put a red banner on the conversation they just
      // opened, about the one they just left. Name check rather than
      // `instanceof DOMException` - fetch and the stream reader are free to
      // reject with either, and only the name is guaranteed.
      if ((err as { name?: string } | null)?.name !== "AbortError") {
        setError(errorMessage(err));
      }
    } finally {
      setStatus(null);
      setSending(false);
    }
  }

  // h-full, not h-screen: this fills `main`, and the (app) layout's h-screen
  // wrapper is what bounds it. h-screen here would be 100vh whatever main
  // actually offers a child. Below md that is not the same number: main is a
  // flex item stretched to the wrapper's 100vh with pt-12 on it, so its content
  // box is 100vh - 3rem and a h-screen child overflows by exactly that padding.
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {loaded && !error && messages.length === 0 && !sending && (
          <p className="mt-16 text-center text-sm text-gray-400">
            등록된 문서에 대해 무엇이든 물어보세요.
          </p>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
        {/* aria-live, because this line is the only feedback between pressing
            전송 and the answer landing, and it is never focused. */}
        <p aria-live="polite" className="text-sm text-gray-400">
          {status}
        </p>
        {/* The answer itself, off screen, because a screen reader was told
            문서 검색 중… and 답변 생성 중… and then nothing at all - the status
            line is emptied the moment the answer lands, so the one thing the
            user asked for was never announced.

            A separate region rather than role="log" on the transcript above:
            that container is populated by the transcript fetch AFTER mount, so
            a live region wrapping it re-announces every message in the history
            on arrival at /chat/{id}. This one only ever changes on `done`.

            Measured in headless Edge against a stub origin: asking inside an
            existing conversation leaves this region holding the answer while
            the status line is empty. The very FIRST answer of a brand new
            conversation is the exception - router.replace reloads the document
            ~76ms later (see below) and takes this region with it, the same
            reload the answer bubble itself survives only by being refetched. */}
        <p aria-live="polite" className="sr-only">
          {announcement}
        </p>
        <div ref={bottomRef} />
      </div>
      <div className="border-t border-gray-200 p-3">
        <ErrorBanner message={error} />
        <form onSubmit={handleSend} className="mt-2 flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="질문을 입력하세요"
            // A placeholder is not an accessible name: it is dropped the moment
            // the field has text, and some screen readers never announce it.
            aria-label="질문"
            className="flex-1 rounded border border-gray-300 px-3 py-2 text-sm"
          />
          <button
            type="submit"
            disabled={sending}
            className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            전송
          </button>
        </form>
      </div>
    </div>
  );
}
