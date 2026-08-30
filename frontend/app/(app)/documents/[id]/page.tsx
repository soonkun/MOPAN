"use client";

import { use, useEffect, useState } from "react";
import { apiFetch, downloadDocument, errorMessage } from "@/lib/api";
import ChunkViewer from "@/components/documents/ChunkViewer";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Chunk, DocumentItem } from "@/lib/types";

// Next 15 made `params` a Promise. A client component cannot await, so it
// unwraps with React 19's `use()`. A synchronous signature is a build error.
export default function DocumentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  // Named `doc`, not `document`: shadowing the global is harmless here today, but
  // the list page's poll gate reads `document.hidden`, and that pattern copied
  // into a file where `document` is a DocumentItem reads the wrong thing in
  // silence.
  const [doc, setDoc] = useState<DocumentItem | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  // Empty is not the same as not-loaded. Without this the list renders
  // "아직 청크가 없습니다." for the length of the fetch - a false statement - and
  // the (0) in the heading then jumps to its real value.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    // The 원문 구조 pane and its GET /api/documents/{id}/structure are gone. It
    // re-parsed the whole file on every request - about 35 seconds of pdfplumber
    // on the 854-page document in this corpus - to fill a pane nobody read.
    Promise.all([
      apiFetch<DocumentItem>(`/api/documents/${id}`),
      apiFetch<Chunk[]>(`/api/documents/${id}/chunks`),
    ])
      .then(([item, chunkList]) => {
        setDoc(item);
        setChunks(chunkList);
      })
      .catch((err) => setError(errorMessage(err)))
      // finally, not a tail of .then: a 404 on the document otherwise leaves the
      // list saying 불러오는 중... forever, under a banner explaining why.
      .finally(() => setLoading(false));
  }, [id]);

  async function download() {
    if (doc === null) return;
    setDownloading(true);
    try {
      await downloadDocument(doc.id, doc.filename);
      setError(null);
    } catch (err) {
      // Where 원본 파일을 더 이상 찾을 수 없습니다. lands when the row outlived
      // its file - the backend answers a Korean 404 and this is what shows it.
      setError(errorMessage(err));
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="min-w-0 break-all text-headline font-medium">{doc?.filename ?? "문서"}</h1>
        {doc && (
          // The accessible name carries the filename: "다운로드" alone would be
          // the same name this control has on every other document.
          <button
            type="button"
            onClick={download}
            disabled={downloading}
            aria-label={`${doc.filename} 원본 파일 다운로드`}
            className="btn-tonal shrink-0"
          >
            {downloading ? "내려받는 중..." : "원본 다운로드"}
          </button>
        )}
      </div>
      {doc?.error_message && <ErrorBanner message={doc.error_message} />}
      <ErrorBanner message={error} />

      {/* One pane, and one line per row. This screen answers "did the chunking
          come out sensibly", which is a question about boundaries, not about
          content - so a row shows where it starts and stops there, and opens to
          the full text and the per-chunk numbers only when asked. */}
      {/* Guarded on the document, not on the banner. A failed DOWNLOAD fills the
          same banner as a failed load, and guarding on `error` made all 1950
          rows vanish the moment one did - the chunk list has nothing to do with
          whether the stored file is still there. A failed LOAD leaves `doc`
          null, which is what should hide it. */}
      {(loading || doc !== null) && (
        <section className="rounded-md bg-surface-container-low p-4">
          <h2 className="mb-4 text-title font-medium text-on-surface">
            청크 목록{!loading && ` (${chunks.length})`}
          </h2>
          {loading ? (
            <p className="text-body text-on-surface-variant">불러오는 중...</p>
          ) : (
            <ChunkViewer chunks={chunks} />
          )}
        </section>
      )}
    </div>
  );
}
