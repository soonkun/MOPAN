"use client";

import { useEffect, useRef } from "react";
import AttachmentChip from "@/components/chat/AttachmentChip";
import type { Attachment } from "@/lib/types";

/** One file the user has chosen. It exists on screen from the moment it is
 * picked, before POST /api/attachments has answered, so that a refusal can be
 * rendered on the chip it belongs to rather than as a page-level banner. */
export type PendingAttachment = {
  /** Local, and NOT the attachment id: a chip exists before the server has
   * given it one, and an upload that is refused never gets one at all. */
  key: string;
  filename: string;
  sizeBytes: number;
  kind: "image" | "document";
  /** A blob: URL made from the File, so an image thumbnail appears immediately
   * and without a second round trip. Revoked by ChatWindow. */
  previewUrl: string | null;
  status: "uploading" | "ready" | "error";
  /** The server's row, once POST /api/attachments has answered. It is what the
   * send carries (its id) and what the sent user turn renders from. */
  attachment: Attachment | null;
  error: string | null;
};

// backend/app/documents/validation.py: ALLOWED_EXTENSIONS | IMAGE_EXTENSIONS.
export const ATTACHMENT_EXTENSIONS = [
  "pdf",
  "docx",
  "txt",
  "md",
  "html",
  "png",
  "jpg",
  "jpeg",
  "webp",
  "gif",
];
const ACCEPT = ATTACHMENT_EXTENSIONS.map((ext) => `.${ext}`).join(",");

// 8 rows of body-lg (26px) plus the textarea's own 8px padding top and bottom.
const MAX_HEIGHT = 8 * 26 + 16;

export default function Composer({
  value,
  onChange,
  onSubmit,
  onFiles,
  attachments,
  onRemove,
  sending,
  onStop,
  textareaRef,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onFiles: (files: File[]) => void;
  attachments: PendingAttachment[];
  onRemove: (key: string) => void;
  sending: boolean;
  onStop: () => void;
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  // Chrome fires keydown(Enter) with isComposing=true while a Hangul syllable
  // is still being composed, but not every engine does; this ref is the second
  // half of the same guard, set from the composition events themselves.
  const composingRef = useRef(false);

  // Auto-grow, 1 to 8 rows. height:auto first, or scrollHeight only ever
  // reports the height it already has and the box can never shrink again after
  // the user deletes a line.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [value, textareaRef]);

  function take(list: FileList | null) {
    if (!list?.length) return;
    onFiles(Array.from(list));
  }

  return (
    // §8: one surface-container block at --radius-xl, no border at rest, a 2px
    // primary outline on focus-within, thumbnails in a row above the textarea
    // and inside the same block.
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      className="rounded-xl bg-surface-container p-2 outline-primary transition-colors duration-150 focus-within:outline focus-within:outline-2"
    >
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 p-1 pb-2">
          {attachments.map((a) => (
            <AttachmentChip
              key={a.key}
              filename={a.filename}
              sizeBytes={a.sizeBytes}
              kind={a.kind}
              src={a.previewUrl}
              status={a.status === "uploading" ? "uploading" : "ready"}
              error={a.error}
              onRemove={() => onRemove(a.key)}
            />
          ))}
        </div>
      )}

      <div className="flex items-end gap-2">
        {/* The keyboard-reachable equivalent of dropping a file on the
            transcript, and the only one: a drop target cannot be focused or
            activated from the keyboard at all. */}
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          aria-label="파일 첨부"
          className="icon-btn"
        >
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          // Cleared after every pick, or choosing the SAME file twice in a row
          // fires no change event and the second attachment never appears.
          onChange={(e) => {
            take(e.target.files);
            e.target.value = "";
          }}
        />

        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onCompositionStart={() => {
            composingRef.current = true;
          }}
          onCompositionEnd={() => {
            composingRef.current = false;
          }}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.shiftKey) return;
            // A Korean user pressing Enter to CONFIRM a Hangul candidate must
            // not send the message - that Enter belongs to the IME. Three
            // checks because no single one is portable: `isComposing` is the
            // standard, keyCode 229 is what the engines that predate it report,
            // and the ref covers an engine that fires compositionend late.
            if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229 || composingRef.current) {
              return;
            }
            // Shift+Enter is handled by the early return above: it falls
            // through to the textarea's own newline insertion.
            e.preventDefault();
            onSubmit();
          }}
          onPaste={(e) => {
            // ONLY when the clipboard actually carries a file. Pasting text has
            // to keep working, so an empty `files` list is left entirely alone -
            // no preventDefault, no interception.
            if (e.clipboardData.files.length === 0) return;
            e.preventDefault();
            take(e.clipboardData.files);
          }}
          placeholder="질문을 입력하세요"
          // A placeholder is not an accessible name: it is dropped the moment
          // the field has text, and some screen readers never announce it.
          aria-label="질문"
          style={{ maxHeight: MAX_HEIGHT }}
          className="min-w-0 flex-1 resize-none bg-transparent px-2 py-2 text-body-lg text-on-surface placeholder:text-on-surface-variant focus:outline-none"
        />

        {/* The two `key`s are load-bearing, and this was measured. Without them
            React reuses ONE <button> DOM node across the branch and only
            rewrites its `type`. A click's activation behaviour reads `type`
            AFTER the listeners have run, so pressing 중지 went: click ->
            onStop -> abort -> the rejection's setState flushes -> the very same
            node is now type="submit" -> the browser submits the form -> the
            question that 중지 had just restored to the composer was sent
            straight back out. Observed as a second `sending` render with no
            second click, and the 중지 button never going away. Distinct keys
            make React replace the node instead, and a detached button has no
            form owner to submit. */}
        {sending ? (
          // The AbortController ChatWindow already held for unmount, surfaced.
          <button
            key="stop"
            type="button"
            onClick={onStop}
            aria-label="답변 생성 중지"
            className="h-10 shrink-0 rounded-full bg-surface-container-high px-5 text-label font-medium text-on-surface transition-colors duration-150 hover:bg-surface-container-highest"
          >
            중지
          </button>
        ) : (
          // Filled when there is something to send, tonal when there is not -
          // the button says whether the composer is ready without a word.
          <button
            key="send"
            type="submit"
            className={`h-10 shrink-0 rounded-full px-5 text-label font-medium transition-colors duration-150 ${
              value.trim()
                ? "bg-primary text-on-primary"
                : "bg-surface-container-high text-on-surface-variant"
            }`}
          >
            전송
          </button>
        )}
      </div>
    </form>
  );
}
