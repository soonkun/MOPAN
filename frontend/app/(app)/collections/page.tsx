"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Collection, DocumentItem, User } from "@/lib/types";

export default function CollectionsPage() {
  const [user, setUser] = useState<User | null>(null);
  // null is "not loaded yet", not "none" - the same distinction the documents
  // page draws, so 분류가 없습니다. never flashes at an admin who has some.
  const [collections, setCollections] = useState<Collection[] | null>(null);
  // null is "the count is unknown", which is not 0. See load().
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [saving, setSaving] = useState(false);
  // One row acts at a time, so one slot rather than a map keyed by id. It is
  // rendered inside the row that produced it: a rename refused with 같은 이름의
  // 분류가 이미 있습니다. has to appear beside the field holding that name.
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Collection | null>(null);

  // The document count comes from ONE list request, not one per row:
  // CollectionResponse does not carry a count, and /api/documents is the same
  // call the documents page already makes. allSettled, not all, because the
  // count is decoration while the list is the page - a failing /api/documents
  // must not blank out the 분류 table. A rejected count leaves `counts` null and
  // the column reads "-" rather than a wrong 0.
  const load = useCallback(async () => {
    const [cols, docs] = await Promise.allSettled([
      apiFetch<Collection[]>("/api/collections"),
      apiFetch<DocumentItem[]>("/api/documents"),
    ]);
    if (cols.status === "fulfilled") setCollections(cols.value);
    if (docs.status === "fulfilled") {
      const tally: Record<string, number> = {};
      for (const doc of docs.value) {
        tally[doc.collection_id] = (tally[doc.collection_id] ?? 0) + 1;
      }
      setCounts(tally);
    }
    setLoadError(cols.status === "rejected" ? errorMessage(cols.reason) : null);
  }, []);

  useEffect(() => {
    apiFetch<User>("/api/auth/me")
      .then(setUser)
      .catch((err) => setLoadError(errorMessage(err)));
    void load();
  }, [load]);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      await apiFetch<Collection>("/api/collections", {
        method: "POST",
        body: JSON.stringify({ name, description: description.trim() || null }),
      });
      setName("");
      setDescription("");
      // Refetch rather than pushing the returned row onto the list: another
      // admin's collection created since this page loaded would otherwise stay
      // invisible until a reload, and its document count would be missing.
      await load();
    } catch (err) {
      setCreateError(errorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  function startEdit(collection: Collection) {
    setEditingId(collection.id);
    setEditName(collection.name);
    setEditDescription(collection.description ?? "");
    setRowError(null);
  }

  async function handleSave(id: string) {
    setSaving(true);
    setRowError(null);
    try {
      // An empty 설명 is sent as an explicit null, which is the only way to
      // clear it - PATCH treats an OMITTED field as "leave this alone".
      const updated = await apiFetch<Collection>(`/api/collections/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: editName, description: editDescription.trim() || null }),
      });
      // The server's object, not the form's. The backend trims the name, so the
      // row has to show what was stored and not what was typed.
      setCollections((prev) => (prev ?? []).map((c) => (c.id === id ? updated : c)));
      setEditingId(null);
    } catch (err) {
      setRowError({ id, message: errorMessage(err) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
      <h1 className="text-headline font-medium">분류 관리</h1>
      <ErrorBanner message={loadError} />

      {/* `user === null` is "not loaded yet", not "not an admin" - branching on
          the role alone tells every admin they lack permission for the length
          of the /api/auth/me round trip. The endpoints answer a non-admin with
          403 관리자 권한이 필요합니다. regardless; this only keeps buttons that
          cannot work off the screen. */}
      {user !== null && user.role !== "admin" ? (
        <p className="text-body text-on-surface-variant">분류 관리는 관리자만 할 수 있습니다.</p>
      ) : (
        <>
          {user !== null && (
            <form onSubmit={handleCreate} className="space-y-3 rounded-md bg-surface-container-low p-4">
              <div className="flex flex-wrap items-end gap-2">
                <div className="flex flex-col gap-1">
                  <label htmlFor="new-collection-name" className="text-body text-on-surface-variant">
                    분류 이름
                  </label>
                  <input
                    id="new-collection-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    required
                    maxLength={255}
                    className="field"
                  />
                </div>
                <div className="flex flex-1 flex-col gap-1">
                  <label htmlFor="new-collection-description" className="text-body text-on-surface-variant">
                    설명 (선택)
                  </label>
                  <input
                    id="new-collection-description"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    className="field w-full"
                  />
                </div>
                <button
                  type="submit"
                  disabled={creating}
                  className="btn-tonal"
                >
                  {creating ? "추가 중..." : "분류 추가"}
                </button>
              </div>
              {/* Under the form, not at the top of the page: 같은 이름의 분류가
                  이미 있습니다. is about the name in the field right above it. */}
              <ErrorBanner message={createError} />
            </form>
          )}

          {collections === null ? (
            <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
          ) : collections.length === 0 ? (
            <p className="py-8 text-center text-body text-on-surface-variant">분류가 없습니다.</p>
          ) : (
            <div className="overflow-x-auto rounded-sm">
              <table className="w-full text-left text-body">
                <thead>
                  <tr className="bg-surface-container-low text-label font-medium text-on-surface-variant">
                    <th scope="col" className="px-3 py-3">분류 이름</th>
                    <th scope="col" className="px-3 py-3">설명</th>
                    <th scope="col" className="px-3 py-3 text-right">문서 수</th>
                    <th scope="col" className="px-3 py-3">등록일</th>
                    <th scope="col" className="px-3 py-3">관리</th>
                  </tr>
                </thead>
                <tbody>
                  {collections.map((c) => {
                    const editing = editingId === c.id;
                    return (
                      <tr key={c.id} className="border-b border-outline-variant align-top">
                        <td className="px-3 py-3">
                          {editing ? (
                            <input
                              value={editName}
                              onChange={(e) => setEditName(e.target.value)}
                              maxLength={255}
                              aria-label={`${c.name} 분류 이름`}
                              className="field h-8 w-full px-2 text-caption"
                            />
                          ) : (
                            c.name
                          )}
                        </td>
                        <td className="px-3 py-3 text-on-surface-variant">
                          {editing ? (
                            <input
                              value={editDescription}
                              onChange={(e) => setEditDescription(e.target.value)}
                              aria-label={`${c.name} 설명`}
                              className="field h-8 w-full px-2 text-caption"
                            />
                          ) : (
                            (c.description ?? "-")
                          )}
                        </td>
                        <td className="px-3 py-3 text-right text-on-surface-variant">
                          {counts === null ? "-" : (counts[c.id] ?? 0)}
                        </td>
                        <td className="px-3 py-3 text-on-surface-variant">
                          {new Date(c.created_at).toLocaleDateString()}
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex gap-2">
                            {editing ? (
                              <>
                                <button
                                  type="button"
                                  onClick={() => void handleSave(c.id)}
                                  disabled={saving}
                                  className="btn-tonal btn-compact"
                                >
                                  저장
                                </button>
                                <button
                                  type="button"
                                  onClick={() => {
                                    setEditingId(null);
                                    setRowError(null);
                                  }}
                                  className="btn-tonal btn-compact"
                                >
                                  취소
                                </button>
                              </>
                            ) : (
                              <>
                                <button
                                  type="button"
                                  onClick={() => startEdit(c)}
                                  className="btn-tonal btn-compact"
                                >
                                  수정
                                </button>
                                <button
                                  type="button"
                                  onClick={() => {
                                    setRowError(null);
                                    setDeleteTarget(c);
                                  }}
                                  className="btn-danger btn-compact"
                                >
                                  삭제
                                </button>
                              </>
                            )}
                          </div>
                          {rowError?.id === c.id && (
                            <div className="mt-2 max-w-sm">
                              <ErrorBanner message={rowError.message} />
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {/* The delete 409 - 문서 N개가 들어 있는 분류는... - arrives after the
          click, so ConfirmDialog runs the request itself and renders the message
          inside the dialog rather than closing first. */}
      {deleteTarget && (
        <ConfirmDialog
          title="분류 삭제"
          message={`'${deleteTarget.name}' 분류를 삭제할까요? 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onClose={() => setDeleteTarget(null)}
          onConfirm={async () => {
            await apiFetch(`/api/collections/${deleteTarget.id}`, { method: "DELETE" });
            await load();
          }}
        />
      )}
    </div>
  );
}
