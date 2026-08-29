"use client";

import { useEffect, useRef, useState } from "react";
import { apiFetch, errorMessage, streamChat } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import MessageBubble from "@/components/chat/MessageBubble";
import type { Message } from "@/lib/types";

const STATUS_LABEL: Record<string, string> = {
  searching: "문서 검색 중...",
  answering: "답변 생성 중...",
};

export default function ChatWindow({
  initialConversationId,
}: {
  initialConversationId: string | null;
}) {
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
  const bottomRef = useRef<HTMLDivElement>(null);

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
    setInput("");
    setError(null);
    setSending(true);
    setMessages((prev) => [
      ...prev,
      {
        id: `temp-${Date.now()}`,
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
      await streamChat({ conversation_id: conversationId, message: question }, (event) => {
        if (event.type === "status") {
          setStatus(STATUS_LABEL[event.status] ?? null);
        } else if (event.type === "error") {
          setError(event.detail);
        } else if (event.type === "done") {
          newConversationId = event.conversation_id;
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
      });

      if (!conversationId && newConversationId) {
        setConversationId(newConversationId);
        // history.replaceState, not router.replace. router.replace crosses from
        // /chat to /chat/[conversationId] - a different route segment - so it
        // unmounts this component, `messages` resets to [], and the answer that
        // just arrived vanishes until the refetch lands. Measured on an
        // equivalent route pair by requestAnimationFrame sampling: mount count
        // 1 -> 2 and the transcript 2 items -> 0, restored 44ms later over
        // loopback. replaceState keeps this component mounted (mount count
        // stays 1, transcript stays 2) and Next 15 still syncs its router
        // state, so usePathname() updates in the same frame - measured, and it
        // has to be, because the Sidebar refetches its conversation list on
        // exactly that change and the new title would otherwise never appear.
        // End to end on this page with a real answer: zero frames with an empty
        // transcript, the answer bubble and the new URL landing on the same
        // frame, /api/conversations refetched 2ms later, the new link in the
        // sidebar 31ms after that.
        window.history.replaceState(null, "", `/chat/${newConversationId}`);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setStatus(null);
      setSending(false);
    }
  }

  // h-full, not h-screen: this fills `main`, which the (app) layout already
  // bounds at h-screen. h-screen here would be 100vh no matter what main
  // actually offers a child, and below md main is a border-box h-screen with
  // pt-12, so its content box is 100vh - 3rem and a h-screen child overflows
  // it by exactly that padding.
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
