"use client";

import { useState } from "react";
import type { Collection, Folder } from "@/lib/types";

/** 탐색기의 왼쪽 트리. 컬렉션이 루트 노드, 그 아래 폴더. 선택은 {collectionId, folderId}
 * 한 쌍이고 folderId null은 컬렉션 루트, collectionId null은 "전체"다.
 *
 * 끌어다 놓기: 표의 행이 dataTransfer "text/mopan-document"로 문서 id를 실어 오면
 * 폴더(또는 컬렉션 루트)가 받아 onDropDocument를 부른다. 터치 기기는 작업 바의
 * "이동"이 대체 경로다 - 계획 문서 1.5. */
export type TreeSelection = { collectionId: string | null; folderId: string | null };

export default function FolderTree({
  collections,
  folders,
  selection,
  onSelect,
  isAdmin,
  onNewFolder,
  onRename,
  onDelete,
  onDropDocument,
  rootCounts,
}: {
  collections: Collection[];
  folders: Record<string, Folder[]>;
  selection: TreeSelection;
  onSelect: (s: TreeSelection) => void;
  isAdmin: boolean;
  onNewFolder: (collectionId: string, parentId: string | null) => void;
  onRename: (folder: Folder) => void;
  onDelete: (folder: Folder) => void;
  onDropDocument: (documentId: string, collectionId: string, folderId: string | null) => void;
  /** 컬렉션 루트에 직접 놓인 문서 수(폴더 없는 문서). */
  rootCounts: Record<string, number>;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  function toggle(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function dropProps(key: string, collectionId: string, folderId: string | null) {
    return {
      onDragOver: (e: React.DragEvent) => {
        if (!e.dataTransfer.types.includes("text/mopan-document")) return;
        e.preventDefault();
        setDropTarget(key);
      },
      onDragLeave: () => setDropTarget((t) => (t === key ? null : t)),
      onDrop: (e: React.DragEvent) => {
        const id = e.dataTransfer.getData("text/mopan-document");
        setDropTarget(null);
        if (id) onDropDocument(id, collectionId, folderId);
      },
    };
  }

  function renderFolder(f: Folder, all: Folder[]) {
    const children = all.filter((c) => c.parent_id === f.id).sort((a, b) => a.name.localeCompare(b.name, "ko"));
    const active = selection.folderId === f.id;
    const key = `f:${f.id}`;
    return (
      <li key={f.id}>
        <div
          className={`group flex items-center gap-1 rounded-md pr-1 ${active ? "bg-primary-container text-on-primary-container" : "hover:bg-surface-container-high"} ${dropTarget === key ? "outline outline-2 outline-primary" : ""}`}
          style={{ paddingLeft: `${f.depth * 12}px` }}
          {...dropProps(key, f.collection_id, f.id)}
        >
          <button
            type="button"
            onClick={() => toggle(f.id)}
            aria-label={collapsed.has(f.id) ? "펼치기" : "접기"}
            className={`h-6 w-5 shrink-0 text-caption ${children.length ? "" : "invisible"}`}
          >
            {collapsed.has(f.id) ? "▸" : "▾"}
          </button>
          <button
            type="button"
            onClick={() => onSelect({ collectionId: f.collection_id, folderId: f.id })}
            className="flex min-w-0 flex-1 items-center gap-1.5 py-1 text-left text-body"
            aria-current={active ? "true" : undefined}
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M3 8a2 2 0 0 1 2-2h3.2l1.8 2H19a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
            </svg>
            <span className="truncate">{f.name}</span>
            {f.total_document_count > 0 && (
              <span
                className="ml-auto shrink-0 text-caption opacity-70"
                title={`하위 포함 ${f.total_document_count}개 · ${(f.total_size_bytes / 1048576).toFixed(1)}MB${f.last_updated_at ? ` · 최근 ${new Date(f.last_updated_at).toLocaleDateString()}` : ""}`}
              >
                {f.total_document_count}
              </span>
            )}
          </button>
          {isAdmin && (
            <span className="hidden shrink-0 gap-0.5 group-hover:flex">
              <button type="button" onClick={() => onNewFolder(f.collection_id, f.id)} className="icon-btn h-6 w-6 text-caption" title="하위 폴더 만들기" aria-label={`${f.name} 밑에 폴더 만들기`}>＋</button>
              <button type="button" onClick={() => onRename(f)} className="icon-btn h-6 w-6 text-caption" title="이름 바꾸기" aria-label={`${f.name} 이름 바꾸기`}>✎</button>
              <button type="button" onClick={() => onDelete(f)} className="icon-btn h-6 w-6 text-caption" title="삭제(비어 있을 때만)" aria-label={`${f.name} 삭제`}>🗑</button>
            </span>
          )}
        </div>
        {!collapsed.has(f.id) && children.length > 0 && <ul>{children.map((c) => renderFolder(c, all))}</ul>}
      </li>
    );
  }

  const allActive = selection.collectionId === null;
  return (
    <nav aria-label="폴더" className="text-body">
      <button
        type="button"
        onClick={() => onSelect({ collectionId: null, folderId: null })}
        className={`mb-1 flex w-full items-center rounded-md px-2 py-1.5 text-left ${allActive ? "bg-primary-container text-on-primary-container" : "hover:bg-surface-container-high"}`}
        aria-current={allActive ? "true" : undefined}
      >
        전체 문서
      </button>
      <ul className="space-y-1">
        {collections.map((c) => {
          const list = folders[c.id] ?? [];
          const roots = list.filter((f) => f.parent_id === null).sort((a, b) => a.name.localeCompare(b.name, "ko"));
          const active = selection.collectionId === c.id && selection.folderId === null;
          const key = `c:${c.id}`;
          return (
            <li key={c.id}>
              <div
                className={`group flex items-center gap-1 rounded-md pr-1 ${active ? "bg-primary-container text-on-primary-container" : "hover:bg-surface-container-high"} ${dropTarget === key ? "outline outline-2 outline-primary" : ""}`}
                {...dropProps(key, c.id, null)}
              >
                <button type="button" onClick={() => toggle(c.id)} aria-label={collapsed.has(c.id) ? "펼치기" : "접기"} className={`h-6 w-5 shrink-0 text-caption ${roots.length ? "" : "invisible"}`}>
                  {collapsed.has(c.id) ? "▸" : "▾"}
                </button>
                <button type="button" onClick={() => onSelect({ collectionId: c.id, folderId: null })} className="flex min-w-0 flex-1 items-center gap-1.5 py-1 text-left font-medium" aria-current={active ? "true" : undefined}>
                  <span className="truncate">{c.name}</span>
                  {(rootCounts[c.id] ?? 0) > 0 && <span className="ml-auto shrink-0 text-caption font-normal opacity-70">{rootCounts[c.id]}</span>}
                </button>
                {isAdmin && (
                  <button type="button" onClick={() => onNewFolder(c.id, null)} className="icon-btn hidden h-6 w-6 shrink-0 text-caption group-hover:flex" title="폴더 만들기" aria-label={`${c.name}에 폴더 만들기`}>＋</button>
                )}
              </div>
              {!collapsed.has(c.id) && roots.length > 0 && <ul>{roots.map((f) => renderFolder(f, list))}</ul>}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
