"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import { ocPath, type Workspace } from "@/lib/code";

interface FileNode {
  name: string;
  path: string;
  type: "file" | "directory";
  ignored: boolean;
}

/** 작업 공간의 파일 - 결과물을 보고 받는 곳. 목록·내용은 OpenCode의 /file API를 프록시로 읽고, 받기는 서버
 * 작업 공간이면 MOPAN이 파일을 그대로 내려 준다(내 컴퓨터 폴더는 이미 내 컴퓨터에 있다). */
const PREVIEW_MIN = 120; // 미리 보기 최소 높이(px) - 제목 줄 + 몇 줄
const LIST_MIN = 160; // 위 파일 목록에 남겨 둘 최소 높이(px)

export default function FilesPanel({ ws, refreshKey, onClose }: { ws: Workspace; refreshKey: number; onClose: () => void }) {
  const [dir, setDir] = useState("");
  const [entries, setEntries] = useState<FileNode[] | null>(null);
  const [preview, setPreview] = useState<{ path: string; content: string; binary: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 미리 보기 높이(px). 목록과 미리 보기 사이 구분선을 끌어 조절한다(소유자 요청 2026-09-26). null이면 45%.
  // 브라우저에 기억해 두어 다음에 열 때도 같다 - 사람마다 코드 줄 수·화면 높이가 다르다.
  const asideRef = useRef<HTMLElement>(null);
  const [previewH, setPreviewH] = useState<number | null>(() => {
    try {
      const v = Number(localStorage.getItem("code.files.previewH"));
      return v >= PREVIEW_MIN ? v : null;
    } catch {
      return null;
    }
  });
  const clampH = (h: number) => {
    const total = asideRef.current?.getBoundingClientRect().height ?? 800;
    return Math.min(Math.max(h, PREVIEW_MIN), Math.max(PREVIEW_MIN, total - LIST_MIN));
  };
  const commitH = (h: number | null) => {
    setPreviewH(h);
    try {
      if (h === null) localStorage.removeItem("code.files.previewH");
      else localStorage.setItem("code.files.previewH", String(h));
    } catch {
      // 저장 못 해도 이번 세션에는 적용된다
    }
  };
  const startDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) => {
      const bottom = asideRef.current?.getBoundingClientRect().bottom ?? window.innerHeight;
      setPreviewH(clampH(bottom - ev.clientY));
    };
    const up = (ev: PointerEvent) => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
      const bottom = asideRef.current?.getBoundingClientRect().bottom ?? window.innerHeight;
      commitH(clampH(bottom - ev.clientY));
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
  };

  const load = useCallback(async () => {
    try {
      const list = await apiFetch<FileNode[]>(ocPath(ws.id, `file?path=${encodeURIComponent(dir || ".")}`));
      setEntries([...list].sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === "directory" ? -1 : 1)));
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [ws.id, dir]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function open(node: FileNode) {
    if (node.type === "directory") {
      setDir(node.path);
      setPreview(null);
      return;
    }
    try {
      const r = await apiFetch<{ type: "text" | "binary"; content?: string }>(ocPath(ws.id, `file/content?path=${encodeURIComponent(node.path)}`));
      setPreview({ path: node.path, content: r.content ?? "", binary: r.type === "binary" });
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  const crumbs = dir ? dir.split("/") : [];
  const downloadHref = (p: string) => `/api/code/workspaces/${encodeURIComponent(ws.id)}/file?path=${encodeURIComponent(p)}`;

  return (
    <aside ref={asideRef} className="flex h-full w-full flex-col bg-surface-container-low md:w-[26rem] xl:w-[34rem]">
      {/* 폭: 대화 열을 max-w-3xl로 좁혀 얻은 공간을 여기에 준다 - 코드 미리 보기 한 줄이 잘리지 않게(2026-09-26). */}
      <div className="flex items-center gap-2 px-3 py-3">
        <h2 className="min-w-0 flex-1 truncate text-label font-medium text-on-surface">{ws.kind === "cowork" ? "작업 파일" : "파일"}</h2>
        <button type="button" className="btn-text btn-compact" onClick={() => void load()}>
          새로고침
        </button>
        {ws.kind !== "local" && (
          <a href={`/api/code/workspaces/${encodeURIComponent(ws.id)}/download`} className="btn-tonal btn-compact">
            전체 zip
          </a>
        )}
        <button type="button" className="icon-btn h-8 w-8" aria-label="파일 패널 닫기" onClick={onClose}>
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </div>
      <nav className="flex flex-wrap items-center gap-1 px-3 text-caption text-on-surface-variant" aria-label="경로">
        <button type="button" className="hover:text-on-surface" onClick={() => (setDir(""), setPreview(null))}>
          {ws.kind === "local" ? ws.name : "/"}
        </button>
        {crumbs.map((c, i) => (
          <span key={i} className="flex items-center gap-1">
            <span>/</span>
            <button type="button" className="hover:text-on-surface" onClick={() => (setDir(crumbs.slice(0, i + 1).join("/")), setPreview(null))}>
              {c}
            </button>
          </span>
        ))}
      </nav>
      {error && <p className="px-3 py-2 text-caption text-error">{error}</p>}
      <ul className="mt-1 min-h-0 flex-1 overflow-y-auto px-2">
        {entries === null && !error && <li className="px-2 py-1 text-caption text-on-surface-variant">불러오는 중...</li>}
        {entries?.length === 0 && <li className="px-2 py-1 text-caption text-on-surface-variant">비어 있습니다. 첨부로 파일을 넣거나 에이전트에게 만들라고 하세요.</li>}
        {entries?.map((n) => (
          <li key={n.path} className="group flex items-center">
            <button
              type="button"
              onClick={() => void open(n)}
              className={`flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1.5 text-left text-label hover:bg-surface-container ${n.ignored ? "text-on-surface-variant opacity-60" : "text-on-surface"} ${preview?.path === n.path ? "bg-surface-container-high" : ""}`}
            >
              <span className="w-4 shrink-0 text-caption text-on-surface-variant">{n.type === "directory" ? "▸" : "·"}</span>
              <span className="truncate font-mono">{n.name}</span>
            </button>
            {n.type === "file" && ws.kind !== "local" && (
              <a href={downloadHref(n.path)} className="btn-text btn-compact opacity-0 group-hover:opacity-100 focus:opacity-100" aria-label={`${n.name} 받기`}>
                받기
              </a>
            )}
          </li>
        ))}
      </ul>
      {preview && (
        <div
          className="flex min-h-0 flex-shrink-0 flex-col overflow-hidden"
          style={previewH === null ? { maxHeight: "45%" } : { height: previewH }}
        >
          {/* 끌어서 높이 조절. 키보드: 위/아래 화살표 24px, 두 번 클릭하면 기본(45%)으로. */}
          <div
            role="separator"
            aria-orientation="horizontal"
            aria-label="미리 보기 높이 조절"
            tabIndex={0}
            onPointerDown={startDrag}
            onDoubleClick={() => commitH(null)}
            onKeyDown={(e) => {
              const cur = previewH ?? (asideRef.current?.getBoundingClientRect().height ?? 800) * 0.45;
              if (e.key === "ArrowUp") commitH(clampH(cur + 24));
              else if (e.key === "ArrowDown") commitH(clampH(cur - 24));
              else return;
              e.preventDefault();
            }}
            className="group flex h-3 shrink-0 cursor-row-resize touch-none items-center justify-center border-t border-outline-variant focus:outline-none focus-visible:bg-primary-container"
          >
            <span aria-hidden="true" className="h-1 w-10 rounded-full bg-outline-variant group-hover:bg-on-surface-variant" />
          </div>
          <div className="flex items-center gap-2 px-3 py-2">
            <span className="min-w-0 flex-1 truncate font-mono text-caption text-on-surface">{preview.path}</span>
            {ws.kind !== "local" && (
              <a href={downloadHref(preview.path)} className="btn-tonal btn-compact">
                받기
              </a>
            )}
          </div>
          <pre className="min-h-0 flex-1 overflow-auto px-3 pb-3 text-caption text-on-surface">
            {preview.binary ? "(이진 파일 - 미리 보기 없음)" : preview.content.length > 30000 ? preview.content.slice(0, 30000) + "\n…(잘림)" : preview.content}
          </pre>
        </div>
      )}
      {ws.kind === "local" && <p className="px-3 py-2 text-caption text-on-surface-variant">내 컴퓨터 경로: {ws.path}</p>}
    </aside>
  );
}
