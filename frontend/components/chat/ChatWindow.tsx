"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
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
  const router = useRouter();
  const [conversationId, setConversationId] = useState<string | null>(initialConversationId);
  const [messages, setMessages] = useState<Message[]>([]);
  // "no messages" and "not loaded yet" are different states - the same
  // distinction the Sidebar draws for its conversation list. Without it, every
  // arrival at /chat/{id} flashes 등록된 문서에 대해 무엇이든 물어보세요. before
  // the transcript lands, including the router.replace() that follows an answer
  // in a brand-new conversation: measured at 40ms over loopback, and it is a
  // network round trip, so it is only ever longer in front of a real user.
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
        router.replace(`/chat/${newConversationId}`);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setStatus(null);
      setSending(false);
    }
  }

  // h-full, not h-screen: this fills `main`, which the (app) layout already
  // bounds at h-screen. h-screen only happened to match because main stretches
  // to 100vh - a coincidence, not a contract, and it double-counts the moment
  // main gains a header or padding (it already has pt-12 below md).
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {loaded && messages.length === 0 && !sending && (
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
