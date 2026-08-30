"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Chunk, Citation } from "@/lib/types";

function label(citation: Citation): string {
  const parts = [citation.filename ?? "출처"];
  if (citation.page !== null) parts.push(`${citation.page}쪽`);
  if (citation.section) parts.push(citation.section);
  return parts.join(", ");
}

export default function CitationBadge({ citation }: { citation: Citation }) {
  const [open, setOpen] = useState(false);
  const [chunk, setChunk] = useState<Chunk | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    // chunk_id is null for an attachment citation (a file the user attached to
    // their own turn - `filename` is set, everything else is null) and for the
    // MCP citations Slice 2/3 adds. See lib/types.ts.
    // Requesting /api/chunks/null is a 422, which would stack a red validation
    // banner above the snippet this citation already carries - a snippet with
    // nothing wrong with it.
    if (!open || chunk || !citation.chunk_id) return;
    // Fetch the FULL chunk, not the 300-char snippet already in the citation.
    apiFetch<Chunk>(`/api/chunks/${citation.chunk_id}`)
      .then(setChunk)
      .catch((err) => setError(errorMessage(err)));
  }, [open, chunk, citation.chunk_id]);

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setOpen(true);
          dialogRef.current?.showModal();
        }}
        title={label(citation)}
        // Without this the accessible name is the literal "[1]", announced as
        // punctuation. The badge is the only route to the source, so it has to
        // say which source it is.
        aria-label={`출처 ${citation.index}: ${label(citation)}`}
        className="mx-0.5 rounded-xs bg-primary-container px-1.5 py-0.5 align-baseline text-caption font-medium text-on-primary-container transition-opacity duration-150 hover:opacity-80"
      >
        [{citation.index}]
      </button>
      {/* A native <dialog> opened with showModal(), not a fixed overlay div:
          the focus trap, Escape-to-close, the inert background and top-layer
          stacking all come with it. Hand-rolled, this modal had no close
          button and no Escape handler, so a keyboard user who opened a
          citation had no way back out of it. onClose - not just the button -
          is what keeps React state in step when Escape closes it natively.

          Portalled to <body>, because a badge now renders INSIDE a markdown
          paragraph and `<dialog>` (with its <div>, <h2> and <p>) is not valid
          inside a <p> - React reported all three as nesting errors, and the
          HTML parser would break the paragraph apart around it on any path
          that goes through parsed markup. A portal renders no DOM at the call
          site at all, so the paragraph stays a paragraph and the dialog's
          top-layer promotion is unaffected. */}
      {typeof document !== "undefined" &&
        createPortal(
          <dialog
            ref={dialogRef}
            aria-label={`출처 ${citation.index}`}
            onClose={() => setOpen(false)}
            // The dialog box itself is the click target only for a click on the
            // backdrop, because the padding lives on the inner div.
            onClick={(e) => {
              if (e.target === dialogRef.current) dialogRef.current?.close();
            }}
            className="max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim"
          >
            <div className="p-6">
              <div className="mb-3 flex items-start justify-between gap-4">
                {/* h2, not p: it is the modal's only title, and without a heading a
                screen reader landing on 닫기 has nothing to jump back to.
                No `uppercase`: this line is a filename plus Korean labels. */}
                <h2 className="text-label font-medium tracking-wide text-on-surface-variant">
                  [{citation.index}] {label(citation)}
                </h2>
                <button
                  type="button"
                  onClick={() => dialogRef.current?.close()}
                  className="btn-text btn-compact shrink-0"
                >
                  닫기
                </button>
              </div>
              <ErrorBanner message={error} />
              <p className="mt-3 whitespace-pre-wrap text-body-lg text-on-surface">
                {chunk ? chunk.content : citation.snippet}
              </p>
            </div>
          </dialog>,
          document.body,
        )}
    </>
  );
}
