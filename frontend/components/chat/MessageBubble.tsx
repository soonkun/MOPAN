"use client";

import { useEffect, useRef, useState } from "react";
import AttachmentChip from "@/components/chat/AttachmentChip";
import Markdown from "@/components/chat/Markdown";
import type { Message } from "@/lib/types";

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
          {message.model && (
            <span className="min-w-0 truncate text-caption text-on-surface-variant">
              <span className="sr-only">답변 모델 </span>
              {message.model}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
