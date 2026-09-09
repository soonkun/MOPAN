"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiFetch, downloadDocument, errorMessage } from "@/lib/api";
import DocumentTable, { STATUS_LABEL, TERMINAL, type SortKey } from "@/components/documents/DocumentTable";
import FolderTree, { type TreeSelection } from "@/components/documents/FolderTree";
import UploadDropzone from "@/components/documents/UploadDropzone";
import PageHeader from "@/components/layout/PageHeader";
import PageShell from "@/components/layout/PageShell";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import { useBodyScrollLock } from "@/components/ui/useBodyScrollLock";
import type { Collection, DocumentItem, Folder, User } from "@/lib/types";

/** 문서 탐색기 (계획 2026-09-08-document-folders 1단계). 왼쪽 트리(분류 → 폴더), 오른쪽
 * 목록. 검색은 문서를 "찾는" 수단이고 폴더는 "책임지는" 수단이라는 것이 이 화면의 전제다.
 * 목록은 서버 페이지(/api/documents/search) - 500건 폴더도 첫 화면이 바로 뜬다. */

const PAGE = 50;

function deleteMessage(count: number, docs: DocumentItem[]): string {
  if (count === 1 && docs[0]) {
    const doc = docs[0];
    const chunks = doc.chunk_count > 0 ? `청크 ${doc.chunk_count.toLocaleString()}개와 그 임베딩이 함께 지워집니다.` : "청크는 아직 하나도 없습니다.";
    const inFlight = TERMINAL.has(doc.status) ? "" : ` 지금 ${STATUS_LABEL[doc.status] ?? doc.status} 상태이므로 진행 중인 처리도 함께 버려집니다.`;
    return `'${doc.filename}' 문서를 삭제합니다. ${chunks}${inFlight} 되돌릴 수 없고, 다시 쓰려면 파일을 올려 처음부터 다시 처리해야 합니다.`;
  }
  const chunks = docs.reduce((n, d) => n + d.chunk_count, 0);
  return `문서 ${count}개를 삭제합니다. 청크 ${chunks.toLocaleString()}개와 임베딩이 함께 지워지며 되돌릴 수 없습니다.`;
}

export default function DocumentsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [folders, setFolders] = useState<Record<string, Folder[]>>({});
  const [rootCounts, setRootCounts] = useState<Record<string, number>>({});
  const [selection, setSelection] = useState<TreeSelection>({ collectionId: null, folderId: null });
  const [recursive, setRecursive] = useState(false);
  const [showSuperseded, setShowSuperseded] = useState(false);
  const [staleOnly, setStaleOnly] = useState(false);
  const [pendingMove, setPendingMove] = useState<{ collectionId: string; folderId: string | null; name: string } | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sort, setSort] = useState<SortKey>("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [deleteTargets, setDeleteTargets] = useState<DocumentItem[] | null>(null);
  const [moveOpen, setMoveOpen] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);
  useBodyScrollLock(treeOpen || moveOpen);
  // 모바일 폴더 시트는 제목 줄 바로 아래까지 올라온다. 제목은 페이지와 함께 스크롤되므로
  // 여는 순간의 위치를 재서 시트의 top으로 쓴다(body 잠금으로 열린 동안은 움직이지 않음).
  const headerRef = useRef<HTMLDivElement>(null);
  const [sheetTop, setSheetTop] = useState(0);
  const openTree = () => {
    const bottom = headerRef.current?.getBoundingClientRect().bottom ?? 0;
    setSheetTop(Math.max(8, Math.round(bottom)));
    setTreeOpen(true);
  };
  const [folderDialog, setFolderDialog] = useState<{ mode: "new" | "rename"; collectionId: string; parentId: string | null; folder?: Folder } | null>(null);
  const [folderName, setFolderName] = useState("");
  const [deleteFolder, setDeleteFolder] = useState<Folder | null>(null);
  const documentsRef = useRef<DocumentItem[]>([]);
  const isAdmin = user?.role === "admin";

  const loadTree = useCallback(async () => {
    try {
      const cols = await apiFetch<Collection[]>("/api/collections");
      setCollections(cols);
      const entries = await Promise.all(
        cols.map(async (c) => [c.id, await apiFetch<Folder[]>(`/api/collections/${c.id}/folders`).catch(() => [] as Folder[])] as const),
      );
      setFolders(Object.fromEntries(entries));
      const roots = await Promise.all(
        cols.map(async (c) => [c.id, (await apiFetch<{ total: number }>(`/api/documents/search?collection_id=${c.id}&root_only=true&limit=1`)).total] as const),
      );
      setRootCounts(Object.fromEntries(roots));
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  const loadDocuments = useCallback(
    async (append = false) => {
      const params = new URLSearchParams({ sort, order, limit: String(PAGE), offset: String(append ? documentsRef.current.length : 0) });
      if (selection.collectionId) params.set("collection_id", selection.collectionId);
      if (selection.folderId) {
        params.set("folder_id", selection.folderId);
        if (recursive) params.set("recursive", "true");
      } else if (selection.collectionId && !recursive) {
        params.set("root_only", "true");
      }
      if (search.trim()) params.set("q", search.trim());
      if (statusFilter) params.set("status", statusFilter);
      if (showSuperseded) params.set("current_only", "false");
      if (staleOnly) params.set("stale_only", "true");
      try {
        const page = await apiFetch<{ total: number; items: DocumentItem[] }>(`/api/documents/search?${params}`);
        const items = append ? [...documentsRef.current, ...page.items] : page.items;
        documentsRef.current = items;
        setDocuments(items);
        setTotal(page.total);
        setError(null);
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setLoading(false);
      }
    },
    [selection, recursive, search, statusFilter, sort, order, showSuperseded, staleOnly],
  );

  useEffect(() => {
    apiFetch<User>("/api/auth/me").then(setUser).catch((err) => setError(errorMessage(err)));
    void loadTree();
  }, [loadTree]);
  useEffect(() => {
    setSelected(new Set());
    void loadDocuments();
  }, [loadDocuments]);
  // 처리 중인 문서가 보이면 3초마다 현재 페이지만 다시 읽는다.
  useEffect(() => {
    const interval = setInterval(() => {
      if (document.hidden) return;
      if (documentsRef.current.every((d) => TERMINAL.has(d.status))) return;
      void loadDocuments();
    }, 3000);
    return () => clearInterval(interval);
  }, [loadDocuments]);

  const refreshAll = useCallback(async () => {
    await Promise.all([loadDocuments(), loadTree()]);
  }, [loadDocuments, loadTree]);

  const download = useCallback(async (doc: DocumentItem) => {
    try {
      await downloadDocument(doc.id, doc.filename);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function toggleAll(checked: boolean) {
    setSelected(checked ? new Set(documents.map((d) => d.id)) : new Set());
  }
  function changeSort(key: SortKey) {
    if (key === sort) setOrder(order === "asc" ? "desc" : "asc");
    else {
      setSort(key);
      setOrder(key === "name" || key === "status" ? "asc" : "desc");
    }
  }

  async function bulk(action: "move" | "delete" | "reprocess", ids: string[], folderId?: string | null, collectionId?: string, reindex = false) {
    const body: Record<string, unknown> = { ids, action };
    if (action === "move") {
      if (folderId) body.folder_id = folderId;
      else body.collection_id = collectionId;
      if (reindex) body.reindex = true;
    }
    const result = await apiFetch<{ done: string[]; failed: { id: string; reason: string }[] }>("/api/documents/bulk", { method: "POST", body: JSON.stringify(body) });
    const verb = action === "move" ? "옮겼습니다" : action === "delete" ? "지웠습니다" : "다시 처리합니다";
    setNotice(`${result.done.length}개를 ${verb}.${result.failed.length ? ` ${result.failed.length}개 실패: ${result.failed[0].reason}` : ""}`);
    setSelected(new Set());
    await refreshAll();
  }

  async function onDropDocument(documentId: string, collectionId: string, folderId: string | null) {
    try {
      await bulk("move", [documentId], folderId, collectionId);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function submitFolder(e: React.FormEvent) {
    e.preventDefault();
    if (!folderDialog || !folderName.trim()) return;
    try {
      if (folderDialog.mode === "new") {
        await apiFetch(`/api/collections/${folderDialog.collectionId}/folders`, { method: "POST", body: JSON.stringify({ name: folderName.trim(), parent_id: folderDialog.parentId }) });
      } else if (folderDialog.folder) {
        await apiFetch(`/api/folders/${folderDialog.folder.id}`, { method: "PATCH", body: JSON.stringify({ name: folderName.trim() }) });
      }
      setFolderDialog(null);
      setFolderName("");
      await loadTree();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const currentCollection = collections.find((c) => c.id === selection.collectionId) ?? null;
  const currentFolders = selection.collectionId ? folders[selection.collectionId] ?? [] : [];
  const breadcrumb = useMemo(() => {
    const chain: Folder[] = [];
    let cur = currentFolders.find((f) => f.id === selection.folderId);
    while (cur) {
      chain.unshift(cur);
      cur = currentFolders.find((f) => f.id === cur!.parent_id);
    }
    return chain;
  }, [currentFolders, selection.folderId]);
  const selectedDocs = documents.filter((d) => selected.has(d.id));

  const tree = (
    <FolderTree
      collections={collections}
      folders={folders}
      selection={selection}
      onSelect={(s) => {
        setSelection(s);
        setTreeOpen(false);
      }}
      isAdmin={isAdmin}
      rootCounts={rootCounts}
      onNewFolder={(collectionId, parentId) => {
        setFolderName("");
        setFolderDialog({ mode: "new", collectionId, parentId });
      }}
      onRename={(folder) => {
        setFolderName(folder.name);
        setFolderDialog({ mode: "rename", collectionId: folder.collection_id, parentId: folder.parent_id, folder });
      }}
      onDelete={setDeleteFolder}
      onDropDocument={onDropDocument}
    />
  );

  return (
    <PageShell>
      <div ref={headerRef}>
        <PageHeader
          title="문서"
          actions={
            <button type="button" onClick={openTree} className="btn-tonal btn-compact md:hidden">
              폴더
            </button>
          }
        />
      </div>
      <ErrorBanner message={error} />
      {notice && <p className="notice">{notice}</p>}

      <div className="md:grid md:grid-cols-[260px_minmax(0,1fr)] md:items-start md:gap-6">
        {/* 데스크톱: 왼쪽 고정 트리. 모바일: 아래 바텀 시트. */}
        <aside className="hidden md:sticky md:top-6 md:block md:max-h-[calc(100vh-6rem)] md:overflow-y-auto md:rounded-md md:bg-surface-container-low md:p-2">{tree}</aside>

        <section className="min-w-0 space-y-3">
          {/* 빵부스러기 */}
          <nav aria-label="현재 위치" className="flex flex-wrap items-center gap-1 text-body">
            <button type="button" onClick={() => setSelection({ collectionId: null, folderId: null })} className={`hover:underline ${!currentCollection ? "font-medium" : "text-on-surface-variant"}`}>
              전체 문서
            </button>
            {currentCollection && (
              <>
                <span className="text-on-surface-variant">/</span>
                <button type="button" onClick={() => setSelection({ collectionId: currentCollection.id, folderId: null })} className={`hover:underline ${breadcrumb.length === 0 ? "font-medium" : "text-on-surface-variant"}`}>
                  {currentCollection.name}
                </button>
              </>
            )}
            {breadcrumb.map((f, i) => (
              <span key={f.id} className="flex items-center gap-1">
                <span className="text-on-surface-variant">/</span>
                <button type="button" onClick={() => setSelection({ collectionId: f.collection_id, folderId: f.id })} className={`hover:underline ${i === breadcrumb.length - 1 ? "font-medium" : "text-on-surface-variant"}`}>
                  {f.name}
                </button>
              </span>
            ))}
            {currentCollection && (
              <label className="ml-auto flex items-center gap-1.5 text-caption text-on-surface-variant">
                <input type="checkbox" checked={recursive} onChange={(e) => setRecursive(e.target.checked)} />
                하위 폴더 포함
              </label>
            )}
          </nav>

          {/* 업로드 - 현재 위치에. */}
          {user === null ? null : isAdmin ? (
            currentCollection ? (
              <UploadDropzone collectionId={currentCollection.id} folderId={selection.folderId} onUploaded={refreshAll} />
            ) : (
              <p className="rounded-md bg-surface-container-low px-4 py-3 text-caption text-on-surface-variant">
                올릴 분류나 폴더를 왼쪽 트리에서 먼저 고르면 여기에 업로드 칸이 나옵니다.
              </p>
            )
          ) : (
            <p className="text-caption text-on-surface-variant">문서 등록은 관리자만 할 수 있습니다.</p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="문서명 / 등록자 검색" aria-label="문서명 / 등록자 검색" className="field min-w-56 flex-1" />
            <label htmlFor="status-filter" className="text-body text-on-surface-variant">상태</label>
            <select id="status-filter" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="field px-2">
              <option value="">전체</option>
              {Object.entries(STATUS_LABEL).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
            <label className="flex items-center gap-1.5 text-caption text-on-surface-variant" title="새 버전으로 교체된 옛 문서도 함께 본다">
              <input type="checkbox" checked={showSuperseded} onChange={(e) => setShowSuperseded(e.target.checked)} />
              이전 버전 포함
            </label>
            <label className="flex items-center gap-1.5 text-caption text-on-surface-variant" title="시행일(또는 등록일)이 고급 설정의 기준 일수를 넘은 현행 문서만">
              <input type="checkbox" checked={staleOnly} onChange={(e) => setStaleOnly(e.target.checked)} />
              점검 필요만
            </label>
            <span className="text-caption text-on-surface-variant">{total.toLocaleString()}개</span>
          </div>

          {loading ? (
            <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
          ) : (
            <>
              <DocumentTable
                documents={documents}
                onDownload={download}
                onDelete={isAdmin ? (doc) => setDeleteTargets([doc]) : undefined}
                selected={isAdmin ? selected : undefined}
                onToggle={isAdmin ? toggle : undefined}
                onToggleAll={isAdmin ? toggleAll : undefined}
                sort={sort}
                order={order}
                onSort={changeSort}
                showFolder={!currentCollection || recursive}
                draggable={isAdmin}
              />
              {documents.length < total && (
                <div className="flex justify-center">
                  <button type="button" onClick={() => void loadDocuments(true)} className="btn-tonal btn-compact">
                    더 보기 ({documents.length}/{total})
                  </button>
                </div>
              )}
            </>
          )}
        </section>
      </div>

      {/* 선택 작업 바 - 선택이 있을 때만 하단에 뜬다. */}
      {isAdmin && selected.size > 0 && (
        <div className="fixed inset-x-0 bottom-0 z-20 border-t border-outline-variant bg-surface-container px-4 py-3 shadow-md">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-2">
            <span className="text-body font-medium">{selected.size}개 선택</span>
            <button type="button" onClick={() => setMoveOpen(true)} className="btn-tonal btn-compact">이동</button>
            <button type="button" onClick={() => void bulk("reprocess", [...selected]).catch((e) => setError(errorMessage(e)))} className="btn-tonal btn-compact">다시 처리</button>
            <button type="button" onClick={() => setDeleteTargets(selectedDocs)} className="btn-tonal btn-compact text-error">삭제</button>
            <button type="button" onClick={() => setSelected(new Set())} className="btn-text btn-compact ml-auto">선택 해제</button>
          </div>
        </div>
      )}

      {/* 모바일 폴더 시트 */}
      {treeOpen && (
        <div className="fixed inset-0 z-30 md:hidden" role="dialog" aria-label="폴더">
          <button type="button" aria-label="닫기" onClick={() => setTreeOpen(false)} className="motion-scrim absolute inset-0 bg-black/40" />
          <div style={{ top: sheetTop }} className="motion-bottom-sheet absolute inset-x-0 bottom-0 flex flex-col rounded-t-lg bg-surface p-4 pb-8 shadow-lg">
            <div className="mb-2 flex shrink-0 items-center justify-between">
              <span className="text-title font-medium">폴더</span>
              <button type="button" onClick={() => setTreeOpen(false)} className="btn-text btn-compact">닫기</button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">{tree}</div>
          </div>
        </div>
      )}

      {/* 이동 대상 선택 */}
      {moveOpen && (
        <div className="fixed inset-0 z-30" role="dialog" aria-label="이동할 폴더">
          <button type="button" aria-label="닫기" onClick={() => setMoveOpen(false)} className="absolute inset-0 bg-black/40" />
          <div className="absolute inset-x-0 bottom-0 max-h-[80vh] overflow-y-auto overscroll-contain rounded-t-lg bg-surface p-4 pb-8 sm:inset-auto sm:left-1/2 sm:top-1/2 sm:w-[28rem] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-lg">
            <p className="mb-2 text-title font-medium">{selected.size}개를 어디로 옮길까요?</p>
            <p className="mb-3 text-caption text-on-surface-variant">다른 분류로 옮기면 청킹 방식이 달라 그 문서를 다시 색인합니다. 확인을 한 번 더 묻습니다.</p>
            <FolderTree
              collections={collections}
              folders={folders}
              selection={{ collectionId: null, folderId: null }}
              onSelect={(s) => {
                setMoveOpen(false);
                if (!s.collectionId) return;
                const crossing = selectedDocs.some((d) => d.collection_id !== s.collectionId);
                if (crossing) {
                  const target = collections.find((c) => c.id === s.collectionId);
                  const folderName = s.folderId ? folders[s.collectionId]?.find((f) => f.id === s.folderId)?.name : null;
                  setPendingMove({ collectionId: s.collectionId, folderId: s.folderId, name: `${target?.name ?? "다른 분류"}${folderName ? ` / ${folderName}` : ""}` });
                  return;
                }
                void bulk("move", [...selected], s.folderId, s.collectionId).catch((e) => setError(errorMessage(e)));
              }}
              isAdmin={false}
              rootCounts={rootCounts}
              onNewFolder={() => undefined}
              onRename={() => undefined}
              onDelete={() => undefined}
              onDropDocument={() => undefined}
            />
          </div>
        </div>
      )}

      {/* 폴더 만들기/이름 바꾸기 */}
      {folderDialog && (
        <div className="fixed inset-0 z-30" role="dialog" aria-label={folderDialog.mode === "new" ? "폴더 만들기" : "이름 바꾸기"}>
          <button type="button" aria-label="닫기" onClick={() => setFolderDialog(null)} className="absolute inset-0 bg-black/40" />
          <form onSubmit={submitFolder} className="absolute left-1/2 top-1/2 w-[min(28rem,90vw)] -translate-x-1/2 -translate-y-1/2 space-y-3 rounded-lg bg-surface p-4">
            <p className="text-title font-medium">{folderDialog.mode === "new" ? "새 폴더" : "이름 바꾸기"}</p>
            <input autoFocus value={folderName} onChange={(e) => setFolderName(e.target.value)} maxLength={200} placeholder="폴더 이름" aria-label="폴더 이름" className="field w-full" />
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setFolderDialog(null)} className="btn-text btn-compact">취소</button>
              <button type="submit" disabled={!folderName.trim()} className="btn-filled btn-compact">{folderDialog.mode === "new" ? "만들기" : "저장"}</button>
            </div>
          </form>
        </div>
      )}

      {pendingMove && (
        <ConfirmDialog
          title="다른 분류로 이동"
          message={`문서 ${selected.size}개를 '${pendingMove.name}'(으)로 옮깁니다. 분류의 청킹 방식이 달라 옮긴 문서는 다시 색인합니다(청크가 새로 만들어지고 임베딩 비용이 듭니다). 처리 중인 문서는 건너뜁니다.`}
          confirmLabel="옮기고 다시 색인"
          onConfirm={async () => {
            const move = pendingMove;
            setPendingMove(null);
            await bulk("move", [...selected], move.folderId, move.collectionId, true);
          }}
          onClose={() => setPendingMove(null)}
        />
      )}

      {deleteFolder && (
        <ConfirmDialog
          title="폴더 삭제"
          message={`'${deleteFolder.name}' 폴더를 지웁니다. 문서나 하위 폴더가 있으면 서버가 거절합니다.`}
          confirmLabel="삭제"
          onConfirm={async () => {
            await apiFetch(`/api/folders/${deleteFolder.id}`, { method: "DELETE" });
            if (selection.folderId === deleteFolder.id) setSelection({ collectionId: deleteFolder.collection_id, folderId: deleteFolder.parent_id });
            await loadTree();
          }}
          onClose={() => setDeleteFolder(null)}
        />
      )}

      {deleteTargets && (
        <ConfirmDialog
          title={deleteTargets.length > 1 ? `문서 ${deleteTargets.length}개 삭제` : "문서 삭제"}
          message={deleteMessage(deleteTargets.length, deleteTargets)}
          confirmLabel="삭제"
          onClose={() => setDeleteTargets(null)}
          onConfirm={async () => {
            try {
              if (deleteTargets.length === 1) await apiFetch(`/api/documents/${deleteTargets[0].id}`, { method: "DELETE" });
              else await bulk("delete", deleteTargets.map((d) => d.id));
            } catch (err) {
              await refreshAll();
              if (err instanceof ApiError && err.status === 404) throw new ApiError(404, "이미 삭제된 문서입니다. 목록을 새로 고쳤습니다.");
              throw err;
            }
            setSelected(new Set());
            await refreshAll();
          }}
        />
      )}
    </PageShell>
  );
}
