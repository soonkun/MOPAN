"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import DocumentTable from "@/components/documents/DocumentTable";
import UploadDropzone from "@/components/documents/UploadDropzone";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Collection, DocumentItem, User } from "@/lib/types";

const TERMINAL = new Set(["indexed", "failed"]);

export default function DocumentsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [selectedCollectionId, setSelectedCollectionId] = useState("");
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const documentsRef = useRef<DocumentItem[]>([]);

  const loadDocuments = useCallback(async () => {
    try {
      const items = await apiFetch<DocumentItem[]>("/api/documents");
      documentsRef.current = items;
      setDocuments(items);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    // No client-side write on page load: the default collection is seeded when
    // the first admin registers, and two tabs would otherwise race into two
    // duplicate collections.
    Promise.all([
      apiFetch<User>("/api/auth/me"),
      apiFetch<Collection[]>("/api/collections"),
    ])
      .then(([me, cols]) => {
        setUser(me);
        setCollections(cols);
        if (cols.length > 0) setSelectedCollectionId(cols[0].id);
      })
      .catch((err) => setError(errorMessage(err)));
    void loadDocuments();
  }, [loadDocuments]);

  useEffect(() => {
    // Poll only while something is actually processing, and stop when the tab is
    // hidden. An unconditional forever-interval is pure waste.
    const interval = setInterval(() => {
      if (document.hidden) return;
      if (documentsRef.current.every((d) => TERMINAL.has(d.status))) return;
      void loadDocuments();
    }, 3000);
    return () => clearInterval(interval);
  }, [loadDocuments]);

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return documents;
    return documents.filter(
      (d) =>
        d.filename.toLowerCase().includes(needle) ||
        (d.collection_name ?? "").toLowerCase().includes(needle) ||
        (d.uploader_email ?? "").toLowerCase().includes(needle),
    );
  }, [documents, filter]);

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <h1 className="text-lg font-semibold">문서</h1>
      <ErrorBanner message={error} />

      {/* `user === null` is "not loaded yet", not "not an admin". Branching on
          user?.role alone told every admin 문서 등록은 관리자만 할 수 있습니다.
          for the length of the /api/auth/me round trip, then swapped it for the
          dropzone and shoved the table down the page. */}
      {user === null ? null : user.role === "admin" ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            {/* htmlFor/id, not a bare <label>: a label that wraps nothing and
                points at nothing names nothing, so the select was announced
                only as a combo box with no idea what it selects. */}
            <label htmlFor="collection-select" className="text-sm text-gray-500">
              분류
            </label>
            <select
              id="collection-select"
              value={selectedCollectionId}
              onChange={(e) => setSelectedCollectionId(e.target.value)}
              className="rounded border border-gray-300 px-2 py-1 text-sm"
            >
              {collections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          {selectedCollectionId && (
            <UploadDropzone collectionId={selectedCollectionId} onUploaded={loadDocuments} />
          )}
        </div>
      ) : (
        <p className="text-sm text-gray-500">문서 등록은 관리자만 할 수 있습니다.</p>
      )}

      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="문서명 / 분류 / 등록자 검색"
        aria-label="문서명 / 분류 / 등록자 검색"
        className="w-full rounded border border-gray-300 px-3 py-2 text-sm"
      />
      <DocumentTable documents={visible} />
    </div>
  );
}
