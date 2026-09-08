"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { DocumentItem, DocumentVersion } from "@/lib/types";

/** 규정 현행화(계획 2단계). 개정판을 "새 버전으로 교체"하면 옛 판은 검색에서 빠지고 여기 이력에
 * 남는다. 되돌리기는 색인이 끝난 버전만. 옛 파일은 내려받을 수 있다. */
export default function VersionsPanel({ doc, isAdmin, onChanged }: { doc: DocumentItem; isAdmin: boolean; onChanged: () => Promise<void> }) {
  const [versions, setVersions] = useState<DocumentVersion[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [effectiveDate, setEffectiveDate] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    try {
      setVersions(await apiFetch<DocumentVersion[]>(`/api/documents/${doc.id}/versions`));
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc.id, doc.is_current, doc.version]);

  async function uploadVersion(file: File) {
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    if (effectiveDate) form.append("effective_date", effectiveDate);
    try {
      await apiFetch(`/api/documents/${doc.id}/versions`, { method: "POST", body: form });
      setEffectiveDate("");
      await Promise.all([load(), onChanged()]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function activate(v: DocumentVersion) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/documents/${doc.id}/versions/${v.id}/activate`, { method: "POST" });
      await Promise.all([load(), onChanged()]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const current = versions?.find((v) => v.is_current);
  return (
    <section className="space-y-3 rounded-md bg-surface-container-low p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="text-title font-medium text-on-surface">버전</h2>
        <span className="text-caption text-on-surface-variant">
          {versions ? `${versions.length}개` : ""}
          {doc.is_current ? " · 이 문서가 현행" : ""}
        </span>
      </div>
      {!doc.is_current && current && (
        <p className="rounded-md bg-surface-container-high px-3 py-2 text-body text-on-surface">
          이 문서는 v{current.version}으로 교체된 옛 버전(v{doc.version})입니다. 검색 근거로는 쓰이지 않습니다.{" "}
          <Link href={`/documents/${current.id}`} className="text-primary underline">현행 보기</Link>
        </p>
      )}
      <ErrorBanner message={error} />
      {versions && (
        <ul className="divide-y divide-outline-variant">
          {versions.map((v) => (
            <li key={v.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-body">
              <span className={`shrink-0 rounded-full px-2 py-0.5 text-caption ${v.is_current ? "bg-primary-container text-on-primary-container" : "bg-surface-container-high text-on-surface-variant"}`}>
                v{v.version}{v.is_current ? " 현행" : ""}
              </span>
              <Link href={`/documents/${v.id}`} className={`min-w-0 flex-1 truncate ${v.id === doc.id ? "font-medium" : "hover:underline"}`}>
                {v.filename}
              </Link>
              <span className="shrink-0 text-caption text-on-surface-variant">
                {v.effective_date && `시행 ${v.effective_date} · `}
                등록 {new Date(v.created_at).toLocaleDateString()}
                {v.uploader_email && ` · ${v.uploader_email}`}
                {v.status !== "indexed" && ` · ${v.status}`}
              </span>
              {isAdmin && !v.is_current && (
                <button type="button" disabled={busy || v.status !== "indexed"} onClick={() => void activate(v)} className="btn-text btn-compact" title={v.status !== "indexed" ? "색인이 끝난 버전만 현행으로 만들 수 있습니다" : "이 버전을 현행으로"}>
                  현행으로
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {isAdmin && (
        <div className="flex flex-wrap items-center gap-2 border-t border-outline-variant pt-3">
          <span className="text-body font-medium">새 버전으로 교체</span>
          <label className="flex items-center gap-1 text-caption text-on-surface-variant">
            시행일
            <input type="date" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} className="field" />
          </label>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.txt,.md,.html,.xlsx,.csv"
            disabled={busy}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void uploadVersion(f);
            }}
            className="text-caption text-on-surface-variant"
            aria-label="새 버전 파일"
          />
          <span className="w-full text-caption text-on-surface-variant">
            올리면 이 파일이 현행이 되고 지금 버전은 검색에서 빠집니다. 색인이 실패하면 자동으로 이전 버전이 다시 현행이 됩니다. 내용이 같은 파일은 거절합니다.
          </span>
        </div>
      )}
    </section>
  );
}
