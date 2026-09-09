"use client";

import { useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";

type Profile = {
  key: string;
  rank: number;
  label: string;
  provider: string;
  model: string;
  dim: number;
  origin: string;
  badges: string[];
  note: string;
  current: boolean;
  local_present: boolean | null;
};
type Status = {
  profile: string | null;
  provider: string;
  model: string;
  dim: number;
  chunk_count: number;
  switch_command: string;
  profiles: Profile[];
};

const BADGE_CLASS: Record<string, string> = {
  "API 비용 발생": "bg-error-container/60 text-on-error-container",
  "문서 외부 전송": "bg-error-container/60 text-on-error-container",
  "로컬 GPU": "bg-primary-container text-on-primary-container",
};

/** 임베딩 프로필 카드. 선택지를 보이되 바꾸는 버튼은 없다 - 임베딩은 문서와 질문이 같은 공간에 있어야
 * 하므로 전환은 전체 재임베딩을 동반하는 배포 절차다(카드 아래 명령). */
export default function EmbeddingSection() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    apiFetch<Status>("/api/admin/models/embedding").then(setStatus).catch((err) => setError(errorMessage(err)));
  }, []);
  return (
    <section className="space-y-3 rounded-md bg-surface-container-low p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 className="text-body font-medium text-on-surface">임베딩 프로필</h3>
        {status && (
          <span className="text-caption text-on-surface-variant">
            현재 {status.model} · {status.dim}차원 · 임베딩된 청크 {status.chunk_count.toLocaleString()}개
          </span>
        )}
      </div>
      <ErrorBanner message={error} />
      {status && (
        <ol className="space-y-2">
          {status.profiles.map((p) => (
            <li key={p.key} className={`rounded-md p-3 ${p.current ? "bg-primary-container/30 outline outline-1 outline-primary" : "bg-surface"}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-caption text-on-surface-variant">{p.rank}순위</span>
                <span className="text-body font-medium text-on-surface">{p.label}</span>
                <code className="text-caption text-on-surface-variant">{p.model}</code>
                {p.current && <span className="rounded-full bg-primary px-2 py-0.5 text-caption text-on-primary">사용 중</span>}
                {p.badges.map((b) => (
                  <span key={b} className={`rounded-full px-2 py-0.5 text-caption ${BADGE_CLASS[b] ?? "bg-surface-container-high text-on-surface-variant"}`}>
                    {b}
                  </span>
                ))}
                {p.provider === "local" && p.local_present === false && (
                  <span className="text-caption text-on-surface-variant">(아직 내려받지 않음 - 선택하면 기동 시 자동 다운로드)</span>
                )}
              </div>
              <p className="mt-1 text-caption text-on-surface-variant">
                {p.origin} · {p.dim}차원. {p.note}
              </p>
            </li>
          ))}
        </ol>
      )}
      {status && (
        <p className="text-caption text-on-surface-variant">
          바꾸려면(재임베딩 동반): <code>{status.switch_command}</code>
        </p>
      )}
    </section>
  );
}
