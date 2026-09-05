"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiFetch, downloadDocument, errorMessage } from "@/lib/api";
import DocumentTable, { STATUS_LABEL, TERMINAL } from "@/components/documents/DocumentTable";
import UploadDropzone from "@/components/documents/UploadDropzone";
import PageShell from "@/components/layout/PageShell";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Collection, DocumentItem, User } from "@/lib/types";

/** What the confirmation says before an unrecoverable delete. It names the file
 * AND its chunk count on purpose: 정말 삭제할까요? next to eight identical rows
 * confirms nothing, whereas 유사상품 심사기준 · 청크 8,342개 is visibly the wrong
 * document when the click was. The chunks and their embeddings cost real money
 * and minutes to rebuild, so the price of getting them back is spelled out too.
 *
 * The 처리 중 clause is not decoration: DELETE does not cancel the arq job that
 * is mid-flight (see the report - the worker finishes, then fails to write to a
 * row that is gone), so deleting now throws that work away. */
function deleteMessage(doc: DocumentItem): string {
  const chunks =
    doc.chunk_count > 0
      ? `청크 ${doc.chunk_count.toLocaleString()}개와 그 임베딩이 함께 지워집니다.`
      : "청크는 아직 하나도 없습니다.";
  const inFlight = TERMINAL.has(doc.status)
    ? ""
    : ` 지금 ${STATUS_LABEL[doc.status] ?? doc.status} 상태이므로 진행 중인 처리도 함께 버려집니다.`;
  return `'${doc.filename}' 문서를 삭제합니다. ${chunks}${inFlight} 되돌릴 수 없고, 다시 쓰려면 파일을 올려 처음부터 다시 처리해야 합니다.`;
}

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
  // The document the 삭제 confirmation is open for. Holding the row, not just
  // the id, is what lets the dialog name the file and its chunk count.
  const [deleteTarget, setDeleteTarget] = useState<DocumentItem | null>(null);
  const documentsRef = useRef<DocumentItem[]>([]);
  // Where the keyboard lands when the 삭제 button that opened the dialog no
  // longer exists - see the dialog's onClose.
  const searchRef = useRef<HTMLInputElement>(null);

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
      <h1 className="text-center text-headline font-medium md:text-left">문서</h1>
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
          ref={searchRef}
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
        <DocumentTable
          documents={visible}
          onDownload={download}
          onDelete={user?.role === "admin" ? setDeleteTarget : undefined}
        />
      )}

      {/* One document at a time, never a bulk action: this destroys chunks and
          embeddings that cost money and minutes to rebuild. ConfirmDialog runs
          the request itself and keeps a failure inside the dialog, which is what
          the 404 path needs - see onConfirm. */}
      {deleteTarget && (
        <ConfirmDialog
          title="문서 삭제"
          message={deleteMessage(deleteTarget)}
          confirmLabel="삭제"
          onClose={() => {
            // Cancel and Escape: the 삭제 button is still in the table and
            // <dialog> hands focus back to it by itself. After a successful
            // delete that button no longer exists, so the native restore has
            // nowhere to land and drops focus on <body> - Tab would then start
            // over from the top of the page. Send it to the search box above
            // the table instead, which is the nearest thing that survives.
            const rowSurvived = documentsRef.current.some((d) => d.id === deleteTarget.id);
            setDeleteTarget(null);
            if (!rowSurvived) searchRef.current?.focus();
          }}
          onConfirm={async () => {
            try {
              await apiFetch(`/api/documents/${deleteTarget.id}`, { method: "DELETE" });
            } catch (err) {
              // 404 means another session already deleted it, so this list is
              // stale whichever way the request went - refetch either way, and
              // say why rather than repeating 문서를 찾을 수 없습니다., which
              // reads like the delete failed.
              await loadDocuments();
              if (err instanceof ApiError && err.status === 404) {
                throw new ApiError(404, "이미 삭제된 문서입니다. 목록을 새로 고쳤습니다.");
              }
              throw err;
            }
            // Refetch rather than splicing the row out: the row is only gone if
            // the server says so, and the poll's 3s window is long enough to
            // paint a list that disagrees with the database.
            await loadDocuments();
          }}
        />
      )}
    </PageShell>
  );
}
