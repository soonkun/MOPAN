"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { apiFetch, errorMessage } from "@/lib/api";
import Markdown from "@/components/chat/Markdown";
import PageShell from "@/components/layout/PageShell";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { ResearchRun } from "@/lib/types";

const RUNNING = new Set(["queued", "planning", "searching", "gap", "synthesis"]);
const STAGE_LABEL: Record<string, string> = {
  queued: "대기",
  planning: "계획",
  planned: "계획 완료",
  searching: "검색",
  searched: "검색 결과",
  gap: "격차 분석",
  gap_done: "격차 분석 종료",
  budget: "예산",
  synthesis: "보고서 작성",
  warning: "주의",
  done: "완료",
  cancelled: "취소",
};

export default function ResearchRunPage() {
  const params = useParams<{ rid: string }>();
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      setRun(await apiFetch<ResearchRun>(`/api/research/runs/${params.rid}`));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [params.rid]);

  useEffect(() => {
    void load();
  }, [load]);
  // 진행 중이면 2초 폴링. arq 잡이라 탭을 닫고 돌아와도 여기서 이어 본다.
  useEffect(() => {
    if (!run || !RUNNING.has(run.status)) return;
    const timer = setInterval(() => void load(), 2000);
    return () => clearInterval(timer);
  }, [run, load]);

  async function cancel() {
    try {
      await apiFetch(`/api/research/runs/${params.rid}/cancel`, { method: "POST" });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function copy() {
    if (!run?.report) return;
    try {
      await navigator.clipboard.writeText(run.report);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 클립보드 권한이 없으면 조용히 - 본문을 직접 긁을 수 있다.
    }
  }

  const running = run ? RUNNING.has(run.status) : false;

  return (
    <PageShell>
      <div className="flex flex-wrap items-center gap-2">
        <Link href={run ? `/research/${run.project_id}` : "/research"} className="icon-btn h-9 w-9" aria-label="방으로">
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="m15 6-6 6 6 6" />
          </svg>
        </Link>
        <h1 className="min-w-0 flex-1 truncate text-headline font-medium">{run?.prompt ?? "리서치"}</h1>
        {running && (
          <button type="button" onClick={() => void cancel()} className="btn-tonal btn-compact">
            취소
          </button>
        )}
      </div>
      <ErrorBanner message={error} />
      {run === null && !error && <p className="text-body text-on-surface-variant">불러오는 중...</p>}

      {run && (
        <>
          {/* 진행 - 세로 단계 목록. 진행 중이면 펼치고, 끝나면 접어 둔다. */}
          <details open={running || run.status !== "done"} className="group rounded-md bg-surface-container-low">
            <summary className="cursor-pointer list-none px-4 py-3 [&::-webkit-details-marker]:hidden">
              <span className="text-title font-medium">
                {running ? "진행 중" : run.status === "done" ? "완료" : run.status === "failed" ? "실패" : "취소됨"}
              </span>
              <span className="ml-2 text-caption text-on-surface-variant">
                {run.steps.length}단계
                {run.model && ` · ${run.model}`}
                {run.reasoning_effort && ` · 추론 ${run.reasoning_effort}`}
                {run.attachment_name && ` · 첨부 ${run.attachment_name}`}
                {run.usage?.evidence_read !== undefined && ` · 읽은 근거 ${run.usage.evidence_read}건`}
                {run.usage?.prompt_tokens !== undefined && ` · 토큰 ${(run.usage.prompt_tokens + (run.usage.completion_tokens ?? 0)).toLocaleString()}`}
              </span>
            </summary>
            <ol className="space-y-1.5 px-4 pb-4 text-body">
              {run.steps.map((s, i) => (
                <li key={i} className="flex gap-3">
                  <span className="w-24 shrink-0 text-caption text-on-surface-variant">{STAGE_LABEL[s.stage] ?? s.stage}</span>
                  <span className={`min-w-0 flex-1 ${s.stage === "warning" ? "text-error" : "text-on-surface"}`}>{s.detail}</span>
                </li>
              ))}
              {running && (
                <li className="flex gap-3">
                  <span className="w-24 shrink-0 text-caption text-on-surface-variant">…</span>
                  <span className="animate-pulse text-on-surface-variant">진행 중입니다. 화면을 떠나도 계속됩니다.</span>
                </li>
              )}
            </ol>
          </details>

          {run.status === "failed" && (
            <div className="rounded-md bg-error-container px-4 py-3 text-body text-on-error-container">
              실행에 실패했습니다. {run.error}
            </div>
          )}

          {run.report && (
            <section className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-title font-medium">보고서</h2>
                <div className="flex items-center gap-2">
                  <span className="text-caption text-on-surface-variant">출처 {run.sources.length}건 · 인용 번호를 누르면 원문이 보입니다</span>
                  <button type="button" onClick={() => void copy()} className="btn-tonal btn-compact">
                    {copied ? "복사됨" : "복사"}
                  </button>
                </div>
              </div>
              <article className="rounded-md bg-surface p-4 sm:p-6">
                <Markdown content={run.report} citations={run.sources} />
              </article>
              {run.sources.length > 0 && (
                <details className="rounded-md bg-surface-container-low">
                  <summary className="cursor-pointer px-4 py-3 text-body font-medium">출처 {run.sources.length}건</summary>
                  <ol className="space-y-2 px-4 pb-4 text-caption text-on-surface-variant">
                    {run.sources.map((c) => (
                      <li key={c.index} className="flex gap-2">
                        <span className="shrink-0 font-medium text-on-surface">[{c.index}]</span>
                        <span className="min-w-0">
                          {c.filename}
                          {c.page && ` p.${c.page}`}
                          {c.section && ` · ${c.section}`}
                          <span className="block truncate">{c.snippet}</span>
                        </span>
                      </li>
                    ))}
                  </ol>
                </details>
              )}
              {run.sub_queries.length > 0 && (
                <p className="text-caption text-on-surface-variant">검색 질의: {run.sub_queries.join(" · ")}</p>
              )}
            </section>
          )}
        </>
      )}
    </PageShell>
  );
}
