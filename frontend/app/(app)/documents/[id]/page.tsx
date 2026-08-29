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
  const [document, setDocument] = useState<DocumentItem | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [blocks, setBlocks] = useState<Block[]>([]);
  // Empty is not the same as not-loaded. Without this the panes render
  // "원문 구조를 불러올 수 없습니다." and "아직 청크가 없습니다." for the length
  // of the fetch - two false statements, and the structure one reads as a hard
  // failure - and the (0) counts in the headings then jump to their real values.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      apiFetch<DocumentItem>(`/api/documents/${id}`),
      apiFetch<Chunk[]>(`/api/documents/${id}/chunks`),
      apiFetch<Block[]>(`/api/documents/${id}/structure`).catch(() => [] as Block[]),
    ])
      .then(([doc, chunkList, blockList]) => {
        setDocument(doc);
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
      <h1 className="text-lg font-semibold">{document?.filename ?? "문서"}</h1>
      {document?.error_message && <ErrorBanner message={document.error_message} />}
      <ErrorBanner message={error} />

      {/* Original structure on the left, chunks on the right: the comparison view
          an admin needs to judge chunking quality.
          tabIndex on the scroll panes, not the sections: a scroll container with
          no focusable content in it cannot be scrolled from the keyboard in
          Chromium, and neither pane has a single link or button in it. */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <section className="rounded border border-gray-200 p-4">
          <h2 className="mb-3 text-sm font-medium text-gray-500">
            원문 구조{!loading && ` (${blocks.length})`}
          </h2>
          <div tabIndex={0} aria-label="원문 구조" className="max-h-[70vh] overflow-y-auto">
            {loading ? (
              <p className="text-sm text-gray-400">불러오는 중...</p>
            ) : (
              <StructureViewer blocks={blocks} />
            )}
          </div>
        </section>
        <section className="rounded border border-gray-200 p-4">
          <h2 className="mb-3 text-sm font-medium text-gray-500">
            청크 목록{!loading && ` (${chunks.length})`}
          </h2>
          <div tabIndex={0} aria-label="청크 목록" className="max-h-[70vh] overflow-y-auto">
            {loading ? (
              <p className="text-sm text-gray-400">불러오는 중...</p>
            ) : (
              <ChunkViewer chunks={chunks} />
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
