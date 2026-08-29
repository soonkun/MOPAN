"use client";

import { use, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ChunkViewer from "@/components/documents/ChunkViewer";
import StructureViewer from "@/components/documents/StructureViewer";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Block, Chunk, DocumentItem } from "@/lib/types";

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
  const [blocks, setBlocks] = useState<Block[]>([]);
  // Empty is not the same as not-loaded. Without this the panes render
  // "원문 구조를 불러올 수 없습니다." and "아직 청크가 없습니다." for the length
  // of the fetch - two false statements, and the structure one reads as a hard
  // failure - and the (0) counts in the headings then jump to their real values.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Swallowed here rather than failing the whole page - the chunks are still
  // worth showing - but kept, not discarded: the backend distinguishes
  // 원본 파일을 더 이상 찾을 수 없습니다. from a parser failure, and a bare
  // `catch(() => [])` replaced both with StructureViewer's generic empty state.
  const [structureError, setStructureError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      apiFetch<DocumentItem>(`/api/documents/${id}`),
      apiFetch<Chunk[]>(`/api/documents/${id}/chunks`),
      apiFetch<Block[]>(`/api/documents/${id}/structure`).catch((err) => {
        setStructureError(errorMessage(err));
        return [] as Block[];
      }),
    ])
      .then(([item, chunkList, blockList]) => {
        setDoc(item);
        setChunks(chunkList);
        setBlocks(blockList);
      })
      .catch((err) => setError(errorMessage(err)))
      // finally, not a tail of .then: a 404 on the document otherwise leaves both
      // panes saying 불러오는 중... forever, under a banner explaining why.
      .finally(() => setLoading(false));
  }, [id]);

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <h1 className="text-lg font-semibold">{doc?.filename ?? "문서"}</h1>
      {doc?.error_message && <ErrorBanner message={doc.error_message} />}
      <ErrorBanner message={error} />

      {/* Original structure on the left, chunks on the right: the comparison view
          an admin needs to judge chunking quality. What it shows is the
          STRUCTURE-aware pass - candidates opened at every heading and at the
          token limit. The semantic merge pass can only delete a boundary that
          pass drew, and on the corpus in the dev database it deletes none (all
          eight adjacent-pair cosines fall between 0.216 and 0.468 against a 0.75
          threshold), so nothing on this screen currently demonstrates semantic
          merging. See StructureSemanticChunking's docstring for the sweep.

          role/aria-label/tabIndex on the scroll panes. The label is the real
          gain: without it a pane focused from the keyboard announces nothing.
          The role is what makes the label legal - ARIA in HTML forbids
          aria-label on a bare div's implicit `generic` role, and a name on a
          generic element may be dropped outright. The tabIndex is belt and
          braces - measured on Edge/Chromium 152, a plain scroller with no
          tabIndex and no focusable content took Tab focus and scrolled on
          PageDown (0 -> 105), because Chrome 127+ ships keyboard-focusable
          scrollers; it is Safari that still needs it. */}
      {error === null && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <section className="rounded border border-gray-200 p-4">
            <h2 className="mb-3 text-sm font-medium text-gray-500">
              원문 구조{!loading && ` (${blocks.length})`}
            </h2>
            <div
              role="region"
              tabIndex={0}
              aria-label="원문 구조"
              className="max-h-[70vh] overflow-y-auto"
            >
              {loading ? (
                <p className="text-sm text-gray-400">불러오는 중...</p>
              ) : structureError ? (
                <p className="text-sm text-red-600">{structureError}</p>
              ) : (
                <StructureViewer blocks={blocks} />
              )}
            </div>
          </section>
          <section className="rounded border border-gray-200 p-4">
            <h2 className="mb-3 text-sm font-medium text-gray-500">
              청크 목록{!loading && ` (${chunks.length})`}
            </h2>
            <div
              role="region"
              tabIndex={0}
              aria-label="청크 목록"
              className="max-h-[70vh] overflow-y-auto"
            >
              {loading ? (
                <p className="text-sm text-gray-400">불러오는 중...</p>
              ) : (
                <ChunkViewer chunks={chunks} />
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
