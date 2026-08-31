"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, downloadDocument, errorMessage } from "@/lib/api";
import DocumentTable, { STATUS_LABEL, TERMINAL } from "@/components/documents/DocumentTable";
import UploadDropzone from "@/components/documents/UploadDropzone";
import PageShell from "@/components/layout/PageShell";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Collection, DocumentItem, User } from "@/lib/types";

export default function DocumentsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [selectedCollectionId, setSelectedCollectionId] = useState("");
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [search, setSearch] = useState("");
  const [collectionFilter, setCollectionFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  // Empty is not the same as not-loaded - the same defect the detail page's
  // `loading` flag fixes. Measured at 2000ms latency / 50kB/s against a database
  // holding four documents: without the flag 문서가 없습니다. was on screen from
  // the first paint and still there 6s later; with it, 불러오는 중... paints
  // instead and 문서가 없습니다. never appears.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const documentsRef = useRef<DocumentItem[]>([]);

  // The collection filter goes to the server - GET /api/documents takes
  // collection_id - so a filtered view does not download the other collections'
  // rows on every 3s poll. Status has no such parameter and is filtered below.
  const loadDocuments = useCallback(async () => {
    const query = collectionFilter ? `?collection_id=${collectionFilter}` : "";
    try {
      const items = await apiFetch<DocumentItem[]>(`/api/documents${query}`);
      documentsRef.current = items;
      setDocuments(items);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [collectionFilter]);

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
  }, []);

  // Separate from the effect above so changing the collection filter refetches
  // the documents alone, not /api/auth/me and /api/collections with them.
  useEffect(() => {
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

  // Here as well as on the detail page: this is the screen the product owner
  // asked for it on, and the detail page is where you already have the document
  // open. Both go through downloadDocument so a missing stored file shows the
  // backend's Korean 404 in this page's banner rather than saving as JSON.
  const download = useCallback(async (doc: DocumentItem) => {
    try {
      await downloadDocument(doc.id, doc.filename);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return documents.filter((d) => {
      if (statusFilter && d.status !== statusFilter) return false;
      if (!needle) return true;
      return (
        d.filename.toLowerCase().includes(needle) ||
        (d.collection_name ?? "").toLowerCase().includes(needle) ||
        (d.uploader_email ?? "").toLowerCase().includes(needle)
      );
    });
  }, [documents, search, statusFilter]);

  return (
    <PageShell>
      <h1 className="text-headline font-medium">문서</h1>
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
            <label htmlFor="collection-select" className="text-body text-on-surface-variant">
              등록할 분류
            </label>
            <select
              id="collection-select"
              value={selectedCollectionId}
              onChange={(e) => setSelectedCollectionId(e.target.value)}
              className="field px-2"
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
        <p className="text-body text-on-surface-variant">문서 등록은 관리자만 할 수 있습니다.</p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="문서명 / 분류 / 등록자 검색"
          aria-label="문서명 / 분류 / 등록자 검색"
          className="field min-w-56 flex-1"
        />
        <label htmlFor="collection-filter" className="text-body text-on-surface-variant">
          분류 필터
        </label>
        <select
          id="collection-filter"
          value={collectionFilter}
          onChange={(e) => setCollectionFilter(e.target.value)}
          className="field px-2"
        >
          <option value="">전체</option>
          {collections.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <label htmlFor="status-filter" className="text-body text-on-surface-variant">
          상태 필터
        </label>
        <select
          id="status-filter"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="field px-2"
        >
          <option value="">전체</option>
          {Object.entries(STATUS_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      {loading ? (
        <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
      ) : (
        <DocumentTable documents={visible} onDownload={download} />
      )}
    </PageShell>
  );
}
