"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";

type IngestStatus = {
  watch_dir: string | null;
  collection: string;
  interval_minutes: number;
  counts: Record<string, number>;
  document_states: Record<string, number>;
  last_scanned_at: string | null;
};

const COUNT_LABEL: Record<string, string> = {
  registered: "등록됨",
  duplicate: "중복(건너뜀)",
  unsupported: "미지원 형식",
  failed: "실패",
  missing: "사라짐",
};
const STATE_LABEL: Record<string, string> = {
  uploaded: "대기",
  parsing: "파싱",
  chunking: "청킹",
  embedding: "임베딩",
  indexed: "완료",
  failed: "실패",
};

/** 감시 폴더 색인 상태. 서버 디렉터리에 파일을 두면 워커가 주기적으로 훑어 등록·색인한다 -
 * 터널 업로드로는 넣을 수 없는 대용량(2만 권)을 위한 경로. */
export default function IngestSection() {
  const [status, setStatus] = useState<IngestStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await apiFetch<IngestStatus>("/api/admin/ingest/status"));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  // 처리 중인 문서가 있으면 5초마다 갱신.
  useEffect(() => {
    const s = status?.document_states ?? {};
    const working = (s.uploaded ?? 0) + (s.parsing ?? 0) + (s.chunking ?? 0) + (s.embedding ?? 0);
    if (!working) return;
    const t = setInterval(() => void load(), 5000);
    return () => clearInterval(t);
  }, [status, load]);

  async function scan() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/api/admin/ingest/scan", { method: "POST" });
      setNotice("스캔을 대기열에 넣었습니다. 새 파일은 등록되어 곧 처리됩니다.");
      setTimeout(() => void load(), 3000);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3 rounded-md bg-surface-container-low p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-body font-medium text-on-surface">감시 폴더 색인</h3>
        {status?.watch_dir && (
          <button type="button" onClick={() => void scan()} disabled={busy} className="btn-tonal btn-compact">
            {busy ? "요청 중..." : "지금 스캔"}
          </button>
        )}
      </div>
      <ErrorBanner message={error} />
      {notice && <p className="text-caption text-on-surface-variant">{notice}</p>}
      {status === null ? (
        !error && <p className="text-caption text-on-surface-variant">불러오는 중...</p>
      ) : !status.watch_dir ? (
        <p className="text-caption text-on-surface-variant">
          꺼져 있습니다. 서버 .env의 INGEST_WATCH_DIR에 폴더 경로를 넣으면 그 안의 문서(하위 폴더 포함)를 {status.interval_minutes}분마다 훑어
          &lsquo;{status.collection}&rsquo; 분류에 등록·색인합니다. 파일은 복사하지 않고 원본을 참조합니다.
        </p>
      ) : (
        <>
          <p className="text-caption text-on-surface-variant">
            <code>{status.watch_dir}</code> → 분류 &lsquo;{status.collection}&rsquo;
            {status.interval_minutes > 0 ? ` · ${status.interval_minutes}분마다 자동 스캔` : " · 자동 스캔 꺼짐"}
            {status.last_scanned_at && ` · 마지막 ${new Date(status.last_scanned_at).toLocaleString()}`}
          </p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(status.counts).map(([k, v]) => (
              <span key={k} className="rounded-full bg-surface-container-high px-2 py-0.5 text-caption text-on-surface">
                {COUNT_LABEL[k] ?? k} {v}
              </span>
            ))}
            {Object.entries(status.document_states).map(([k, v]) => (
              <span key={k} className={`rounded-full px-2 py-0.5 text-caption ${k === "indexed" ? "bg-primary-container text-on-primary-container" : k === "failed" ? "bg-error-container text-on-error-container" : "bg-surface-container-high text-on-surface"}`}>
                문서 {STATE_LABEL[k] ?? k} {v}
              </span>
            ))}
            {Object.keys(status.counts).length === 0 && <span className="text-caption text-on-surface-variant">아직 스캔한 파일이 없습니다.</span>}
          </div>
          <p className="text-caption text-on-surface-variant">
            같은 내용의 파일은 중복으로 건너뛰고, 사라진 파일은 표시만 합니다(문서는 지우지 않음). 등록된 문서는 문서 탐색기의 해당 분류에서 폴더 그대로 보입니다.
          </p>
        </>
      )}
    </section>
  );
}
