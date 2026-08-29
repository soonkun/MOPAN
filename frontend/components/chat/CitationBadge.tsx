"use client";

import { useEffect, useRef, useState } from "react";
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
    // chunk_id is null for the MCP citations Slice 2/3 adds - see lib/types.ts.
    // Requesting /api/chunks/null is a 422, and its Korean validation fallback
    // would replace the snippet this citation already carries with an error.
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
        className="mx-0.5 rounded bg-gray-200 px-1.5 py-0.5 align-baseline text-xs text-gray-700 hover:bg-gray-300"
      >
        [{citation.index}]
      </button>
      {/* A native <dialog> opened with showModal(), not a fixed overlay div:
          the focus trap, Escape-to-close, the inert background and top-layer
          stacking all come with it. Hand-rolled, this modal had no close
          button and no Escape handler, so a keyboard user who opened a
          citation had no way back out of it. onClose - not just the button -
          is what keeps React state in step when Escape closes it natively. */}
      <dialog
        ref={dialogRef}
        aria-label={`출처 ${citation.index}`}
        onClose={() => setOpen(false)}
        // The dialog box itself is the click target only for a click on the
        // backdrop, because the padding lives on the inner div.
        onClick={(e) => {
          if (e.target === dialogRef.current) dialogRef.current?.close();
        }}
        className="max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded border border-gray-200 bg-white p-0 text-gray-900 backdrop:bg-black/30"
      >
        <div className="p-4">
          <div className="mb-2 flex items-start justify-between gap-4">
            {/* No `uppercase`: this line is a filename plus Korean labels. */}
            <p className="text-xs tracking-wide text-gray-400">
              [{citation.index}] {label(citation)}
            </p>
            <button
              type="button"
              onClick={() => dialogRef.current?.close()}
              className="shrink-0 rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-100"
            >
              닫기
            </button>
          </div>
          <ErrorBanner message={error} />
          <p className="mt-2 whitespace-pre-wrap text-sm text-gray-800">
            {chunk ? chunk.content : citation.snippet}
          </p>
        </div>
      </dialog>
    </>
  );
}
