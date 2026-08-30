"use client";

import { useEffect, useRef, useState } from "react";
import AttachmentChip from "@/components/chat/AttachmentChip";
import Markdown from "@/components/chat/Markdown";
import TraceDialog from "@/components/chat/TraceDialog";
import { apiFetch, errorMessage } from "@/lib/api";
import type { Feedback, Message } from "@/lib/types";

export default function MessageBubble({
  message,
  onNotify,
}: {
  message: Message;
  /** ChatWindow's shared live region. Announcing 복사됨 from a region inside
   * this component would put one live region per message on the page. */
  onNotify?: (text: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Seeded from the server and owned here afterwards. The transcript carries the
  // caller's rating, so a reload shows it; a click updates this copy rather than
  // refetching the whole conversation to change one row.
  const [feedback, setFeedback] = useState<Feedback | null>(message.feedback);
  const [commenting, setCommenting] = useState(false);
  const [comment, setComment] = useState(message.feedback?.comment ?? "");
  const [traceOpen, setTraceOpen] = useState(false);

  async function rate(rating: "up" | "down", withComment?: string) {
    try {
      const saved = await apiFetch<Feedback>(`/api/messages/${message.id}/feedback`, {
        method: "PUT",
        body: JSON.stringify({ rating, comment: withComment ?? feedback?.comment ?? null }),
      });
      setFeedback(saved);
      setComment(saved.comment ?? "");
      // 👎 opens the comment box, 👍 does not: a complaint is the one that is
      // worth a sentence, and asking every satisfied user to type something is
      // how a feedback control stops being clicked at all.
      setCommenting(rating === "down");
      onNotify?.(rating === "up" ? "도움이 됨으로 표시했습니다." : "도움이 안 됨으로 표시했습니다.");
    } catch (err) {
      onNotify?.(errorMessage(err));
    }
  }

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  async function copy() {
    try {
      // The RAW markdown, not the rendered text: what the user wants out of an
      // answer is the thing they can paste back into a document with its list
      // and its code fence intact.
      await navigator.clipboard.writeText(message.content);
    } catch {
      // writeText rejects on a non-secure origin and on a denied permission.
      onNotify?.("복사하지 못했습니다.");
      return;
    }
    setCopied(true);
    onNotify?.("복사됨");
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(false), 2000);
  }

  // §8, and the single biggest structural difference from a generic chat UI:
  // only the USER's message is bubbled. The assistant's answer renders flat on
  // the page surface at reading size with the gradient sparkle at its head, so
  // it reads as a document rather than as a text message. Two return paths
  // rather than one with ternaries everywhere, because the two are not the
  // same shape any more.
  if (message.role === "user") {
    return (
      <div className="flex flex-col items-end gap-2">
        {message.attachments.length > 0 && (
          // A reloaded transcript has no other record of what was sent with the
          // question, so these render from message.attachments and not only
          // from the live composer state.
          <div className="flex max-w-[75%] flex-wrap justify-end gap-2">
            {message.attachments.map((a) => (
              <AttachmentChip
                key={a.id}
                filename={a.filename}
                sizeBytes={a.size_bytes}
                kind={a.kind}
                // Owner-scoped and served `inline` with `nosniff` for images
                // only; a document id here would download, which is why only an
                // image gets a src.
                src={a.kind === "image" ? `/api/attachments/${a.id}/content` : null}
              />
            ))}
          </div>
        )}
        <div className="max-w-[75%] whitespace-pre-wrap rounded-md bg-surface-container px-4 py-3 text-body-lg text-on-surface">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="group flex gap-4">
      {/* aria-hidden: decorative. The role is already carried by the layout and
          by the off-screen live region in ChatWindow. */}
      <span aria-hidden="true" className="sparkle mt-1 h-5 w-5 shrink-0" />
      {/* min-w-0 so a long unbroken token wraps instead of widening the flex
          row past the transcript column. */}
      <div className="min-w-0 flex-1">
        {/* No whitespace-pre-wrap any more: markdown owns the block structure,
            and pre-wrap would double every blank line between paragraphs. */}
        {message.citations.length === 0 && (
          // See the note at the top of this component: the prompt cannot
          // guarantee grounding, so the absence of a citation is treated as
          // what it is - an answer that cannot be shown to come from the
          // corpus. role="note", not "alert": nothing failed, and an assertive
          // live region would interrupt the answer being announced.
          <p
            role="note"
            className="mb-3 rounded-md bg-surface-container-high px-3 py-2 text-body text-on-surface-variant"
          >
            등록된 문서에서 근거를 찾지 못한 답변입니다. 사실 여부를 직접 확인해 주세요.
          </p>
        )}
        <Markdown content={message.content} citations={message.citations} />
        {message.citations.length > 0 && (
          <div className="mt-4 border-t border-outline-variant pt-3 text-caption text-on-surface-variant">
            {message.citations.map((c) => (
              // index, not chunk_id: chunk_id is null for an MCP citation, and
              // two of them on one message would collide on a null key. index
              // is unique per message by construction - the backend assigns it
              // with enumerate(used, start=1).
              <div key={c.index} className="truncate">
                [{c.index}] {c.filename ?? "출처"}
                {c.page !== null ? `, ${c.page}쪽` : ""}
                {c.section ? `, ${c.section}` : ""}
              </div>
            ))}
          </div>
        )}
        {/* Always in the DOM, never revealed by hover alone: a control that
            appears only on :hover is unreachable by keyboard and invisible on
            touch. It is quiet at rest and darkens on hover instead. */}
        <div className="mt-2 flex items-center gap-2">
          <button
            type="button"
            onClick={() => void copy()}
            aria-label="답변 복사"
            className="inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-caption text-on-surface-variant transition-colors duration-150 hover:bg-surface-container"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="9" y="9" width="11" height="11" rx="2" />
              <path d="M5 15V5a2 2 0 0 1 2-2h10" />
            </svg>
            {copied ? "복사됨" : "복사"}
          </button>
          {/* Quiet, and directly under the citations, because it answers the
              same question they do: where did this come from. A user comparing
              two answers to the same question has no other way to tell which
              model gave which - and this is the resolved provider id, so it
              names the exact snapshot, not just the family.
              Null on a user turn and on any answer written before the model
              became a per-question choice. */}
          <button
            type="button"
            onClick={() => void rate("up")}
            aria-label="도움이 됨"
            aria-pressed={feedback?.rating === "up"}
            className={`inline-flex h-8 w-8 items-center justify-center rounded-full transition-colors duration-150 hover:bg-surface-container ${
              feedback?.rating === "up" ? "text-primary" : "text-on-surface-variant"
            }`}
          >
            <svg
              viewBox="0 0 24 24"
              className="h-4 w-4"
              fill={feedback?.rating === "up" ? "currentColor" : "none"}
              stroke="currentColor"
              strokeWidth="1.5"
            >
              {/* Two subpaths, so the palm reads as a thumb rather than as the
                  share arrow a single outline collapses into at 16px. */}
              <path d="M7 10v12" />
              <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
            </svg>
          </button>
          <button
            type="button"
            onClick={() => void rate("down")}
            aria-label="도움이 안 됨"
            aria-pressed={feedback?.rating === "down"}
            className={`inline-flex h-8 w-8 items-center justify-center rounded-full transition-colors duration-150 hover:bg-surface-container ${
              feedback?.rating === "down" ? "text-error" : "text-on-surface-variant"
            }`}
          >
            <svg
              viewBox="0 0 24 24"
              className="h-4 w-4"
              fill={feedback?.rating === "down" ? "currentColor" : "none"}
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <path d="M17 14V2" />
              <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z" />
            </svg>
          </button>
          {/* Quiet and always present, like 복사: the answer to "why did it not
              use my document" has to be reachable from the answer itself. */}
          <button
            type="button"
            onClick={() => setTraceOpen(true)}
            className="inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-caption text-on-surface-variant transition-colors duration-150 hover:bg-surface-container"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="11" cy="11" r="7" />
              <path d="M20 20l-4.5-4.5" />
            </svg>
            추적
          </button>
          {/* Before the model, because it is the coarser fact: an agent decides
              the prompt, the corpus scope and the tool list, and the model is
              one of the things it decides. Absent for the default agent, which
              is the app answering as it always did and needs no label. */}
          {message.agent_name && (
            <span className="min-w-0 truncate text-caption text-on-surface-variant">
              <span className="sr-only">답변 에이전트 </span>
              {message.agent_name}
            </span>
          )}
          {message.model && (
            <span className="min-w-0 truncate text-caption text-on-surface-variant">
              <span className="sr-only">답변 모델 </span>
              {message.model}
            </span>
          )}
        </div>
        {commenting && (
          <form
            className="mt-2 flex flex-wrap items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void rate(feedback?.rating ?? "down", comment.trim());
              setCommenting(false);
            }}
          >
            <label htmlFor={`comment-${message.id}`} className="sr-only">
              피드백 의견
            </label>
            <input
              id={`comment-${message.id}`}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              maxLength={1000}
              placeholder="무엇이 잘못되었는지 알려주세요 (선택)"
              className="field min-w-0 flex-1"
            />
            <button type="submit" className="btn-tonal btn-compact">
              저장
            </button>
            <button type="button" onClick={() => setCommenting(false)} className="btn-text btn-compact">
              닫기
            </button>
          </form>
        )}
        {!commenting && feedback?.comment && (
          <p className="mt-2 text-caption text-on-surface-variant">의견: {feedback.comment}</p>
        )}
      </div>
      {traceOpen && <TraceDialog messageId={message.id} onClose={() => setTraceOpen(false)} />}
    </div>
  );
}
