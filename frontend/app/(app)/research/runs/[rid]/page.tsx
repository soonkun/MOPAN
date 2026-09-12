"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { apiFetch, errorMessage } from "@/lib/api";
import Markdown from "@/components/chat/Markdown";
import PageShell from "@/components/layout/PageShell";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { ResearchProject, ResearchRun, ResearchScope } from "@/lib/types";

/** 리서치 한 건 = 보고서 한 편. 위에서 아래로 "무엇을 검토했나(검토 대상) → 어떻게 진행됐나
 * (단계·관점) → 보고서 → 출처". 진행 중에는 단계가 펼쳐지고, 끝나면 보고서가 주역이 된다. */

const RUNNING = new Set(["queued", "planning", "searching", "gap", "synthesis"]);
const STAGE_LABEL: Record<string, string> = {
  queued: "대기",
  scoping: "검토 대상",
  scoped: "검토 대상",
  planning: "계획",
  planned: "검색 관점",
  searching: "검색",
  searched: "검색 결과",
  gap: "보완 검색",
  gap_done: "보완 종료",
  budget: "예산",
  synthesis: "보고서 작성",
  warning: "주의",
  done: "완료",
  cancelled: "취소",
};
// 상태별 진행 단계(막대). 실제 단계 이벤트가 아니라 사용자가 보는 큰 흐름 넷.
const PHASES: { key: string; label: string; statuses: string[] }[] = [
  { key: "plan", label: "검토 대상·계획", statuses: ["queued", "planning"] },
  { key: "search", label: "검색", statuses: ["searching"] },
  { key: "gap", label: "보완 검색", statuses: ["gap"] },
  { key: "write", label: "보고서 작성", statuses: ["synthesis"] },
];
const SCOPE_LABELS: { key: keyof ResearchScope; label: string }[] = [
  { key: "goals", label: "연구 목표" },
  { key: "targets", label: "연구 대상" },
  { key: "methods", label: "핵심 방법" },
  { key: "outputs", label: "최종 산출물" },
  { key: "prior_work", label: "선행·연계 연구" },
  { key: "keywords", label: "핵심어" },
];

function elapsed(from: string, to: string | null): string {
  const ms = (to ? new Date(to).getTime() : Date.now()) - new Date(from).getTime();
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 60 ? `${s}초` : `${Math.floor(s / 60)}분 ${s % 60}초`;
}

export default function ResearchRunPage() {
  const params = useParams<{ rid: string }>();
  const [run, setRun] = useState<ResearchRun | null>(null);
  const [project, setProject] = useState<ResearchProject | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [tick, setTick] = useState(0);

  const load = useCallback(async () => {
    try {
      const r = await apiFetch<ResearchRun>(`/api/research/runs/${params.rid}`);
      setRun(r);
      setError(null);
      if (!project) apiFetch<ResearchProject>(`/api/research/projects/${r.project_id}`).then(setProject).catch(() => null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [params.rid, project]);

  useEffect(() => {
    void load();
  }, [load]);
  // 진행 중이면 2초 폴링. arq 잡이라 탭을 닫고 돌아와도 여기서 이어 본다. 경과 시간은 1초마다.
  useEffect(() => {
    if (!run || !RUNNING.has(run.status)) return;
    const poll = setInterval(() => void load(), 2000);
    const clock = setInterval(() => setTick((t) => t + 1), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(clock);
    };
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
  const phaseIndex = run ? PHASES.findIndex((p) => p.statuses.includes(run.status)) : -1;
  const isDuplication = project?.template_id === "duplication";
  const scope = run?.scope ?? null;
  const startedAt = run?.steps[0]?.at ?? run?.created_at ?? null;
  void tick;

  return (
    <PageShell>
      {/* 머리 줄 규칙은 PageHeader와 같다(햄버거 자리 pl-12, 높이 min-h-10). */}
      <div className="flex min-h-10 items-center gap-2 pl-12 md:pl-0">
        <Link href={run ? `/research/${run.project_id}` : "/research"} className="icon-btn h-9 w-9 shrink-0" aria-label="방으로">
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="m15 6-6 6 6 6" />
          </svg>
        </Link>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-headline font-medium">{scope?.title || run?.attachment_name || run?.prompt || "리서치"}</h1>
          {project && <p className="truncate text-caption text-on-surface-variant">{project.name}</p>}
        </div>
        {running && (
          <button type="button" onClick={() => void cancel()} className="btn-tonal btn-compact">
            취소
          </button>
        )}
        {run?.status === "done" && run.report && (
          <a href={`/api/research/runs/${run.id}/pdf`} className="btn-filled btn-compact" download>
            PDF 내려받기
          </a>
        )}
      </div>
      <ErrorBanner message={error} />
      {run === null && !error && <p className="text-body text-on-surface-variant">불러오는 중...</p>}

      {run && (
        <>
          {/* 진행 막대 - 네 단계. */}
          {running && (
            <div className="rounded-md bg-surface-container-low p-4">
              <ol className="grid grid-cols-4 gap-2 text-caption">
                {PHASES.map((p, i) => (
                  <li key={p.key} className="min-w-0">
                    <div className={`h-1.5 rounded-full ${i < phaseIndex ? "bg-primary" : i === phaseIndex ? "bg-primary animate-pulse" : "bg-surface-container-highest"}`} />
                    <span className={`mt-1 block truncate ${i === phaseIndex ? "font-medium text-on-surface" : "text-on-surface-variant"}`}>{p.label}</span>
                  </li>
                ))}
              </ol>
              <p className="mt-3 text-body text-on-surface-variant">
                {run.steps.length > 0 ? run.steps[run.steps.length - 1].detail : "시작을 기다리는 중"}
                {startedAt && <span className="ml-2 text-caption">· {elapsed(startedAt, null)} 경과</span>}
              </p>
              <p className="mt-1 text-caption text-on-surface-variant">화면을 떠나도 계속되며, 방의 보고서 목록에서 다시 열 수 있습니다.</p>
            </div>
          )}

          {/* 검토 대상 - 첨부를 먼저 읽어 뽑은 구조화 요약. */}
          {scope && (
            <section className="rounded-md bg-surface-container-low p-4">
              <h2 className="text-title font-medium">검토 대상</h2>
              <p className="mt-1 text-caption text-on-surface-variant">
                {run.attachment_name ? `${run.attachment_name}에서 읽어 정리한 내용입니다.` : "첨부에서 읽어 정리한 내용입니다."} 검색 관점은 여기서 나옵니다.
              </p>
              <dl className="mt-3 grid gap-x-6 gap-y-2 text-body sm:grid-cols-2">
                {SCOPE_LABELS.map(({ key, label }) => {
                  const value = scope[key];
                  if (!Array.isArray(value) || value.length === 0) return null;
                  return (
                    <div key={key} className="min-w-0">
                      <dt className="text-caption text-on-surface-variant">{label}</dt>
                      <dd className="break-keep text-on-surface">{key === "keywords" ? value.join(" · ") : value.map((v, i) => <span key={i} className="block">· {v}</span>)}</dd>
                    </div>
                  );
                })}
              </dl>
            </section>
          )}

          {/* 검색 관점 - 계획이 서는 순간부터 보인다. */}
          {run.sub_queries.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-caption text-on-surface-variant">검색 관점 {run.sub_queries.length}개</span>
              {run.sub_queries.map((q, i) => (
                <span key={i} className="rounded-full bg-surface-container px-3 py-1 text-caption text-on-surface">
                  {q}
                </span>
              ))}
            </div>
          )}

          {/* 단계 기록 - 진행 중엔 펼치고, 끝나면 접는다. */}
          <details open={running || (run.status !== "done")} className="group rounded-md bg-surface-container-low">
            <summary className="cursor-pointer list-none px-4 py-3 [&::-webkit-details-marker]:hidden">
              <span className="text-title font-medium">
                {running ? "진행 기록" : run.status === "done" ? "진행 기록" : run.status === "failed" ? "실패" : "취소됨"}
              </span>
              <span className="ml-2 text-caption text-on-surface-variant">
                {run.steps.length}단계
                {startedAt && run.finished_at && ` · ${elapsed(startedAt, run.finished_at)}`}
                {run.model && ` · ${run.model}`}
                {run.reasoning_effort && ` · 추론 ${run.reasoning_effort}`}
                {run.usage?.evidence_read !== undefined && ` · 읽은 근거 ${run.usage.evidence_read}건`}
                {run.usage?.prompt_tokens !== undefined && ` · 토큰 ${(run.usage.prompt_tokens + (run.usage.completion_tokens ?? 0)).toLocaleString()}`}
              </span>
            </summary>
            <ol className="space-y-1.5 px-4 pb-4 text-body">
              {run.steps.map((s, i) => (
                <li key={i} className="flex gap-3">
                  <span className="w-24 shrink-0 text-caption text-on-surface-variant">{STAGE_LABEL[s.stage] ?? s.stage}</span>
                  <span className={`min-w-0 flex-1 break-keep ${s.stage === "warning" ? "text-error" : "text-on-surface"}`}>{s.detail}</span>
                </li>
              ))}
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
              {isDuplication && (
                <p className="rounded-md bg-surface-container px-4 py-2 text-caption text-on-surface-variant">
                  이 판정은 등록된 문서 안에서 찾은 근거로 만든 참고 자료입니다. 중복 여부의 최종 판단은 심사위원이 합니다. 근거가 인용된 문장만 사실로
                  보고, “확인 불가”로 표시된 항목은 원문을 직접 확인해 주세요.
                </p>
              )}
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
            </section>
          )}
        </>
      )}
    </PageShell>
  );
}
