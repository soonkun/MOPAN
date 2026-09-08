"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { apiFetch, errorMessage } from "@/lib/api";
import PageShell from "@/components/layout/PageShell";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type {
  AnswerModel,
  Collection,
  ResearchInstruction,
  ResearchProject,
  ResearchRunSummary,
  User,
} from "@/lib/types";

const STATUS_LABEL: Record<string, string> = {
  queued: "대기",
  planning: "계획",
  searching: "검색",
  gap: "격차 분석",
  synthesis: "보고서 작성",
  done: "완료",
  failed: "실패",
  cancelled: "취소",
};
const RUNNING = new Set(["queued", "planning", "searching", "gap", "synthesis"]);
const EFFORTS = [
  { id: "", label: "모델 기본" },
  { id: "minimal", label: "즉시" },
  { id: "low", label: "낮음" },
  { id: "medium", label: "중간" },
  { id: "high", label: "깊이" },
];
const BUDGET_FIELDS: { key: keyof ResearchProject["budget"]; label: string; min: number; max: number; help: string }[] = [
  { key: "sub_queries", label: "하위 질의 수", min: 1, max: 12, help: "질문을 몇 갈래로 나눠 검색할지" },
  { key: "top_k_per_query", label: "질의당 근거 수", min: 1, max: 15, help: "질의 하나가 가져오는 청크 수" },
  { key: "gap_rounds", label: "격차 분석 횟수", min: 0, max: 3, help: "빈 곳을 찾아 다시 검색하는 횟수. 0이면 생략" },
  { key: "max_evidence_chunks", label: "보고서 근거 상한", min: 5, max: 80, help: "종합 단계에 넘기는 최대 근거 수" },
];

type Draft = {
  name: string;
  description: string;
  instructions: string;
  model: string;
  reasoning_effort: string;
  collection_ids: string[];
  budget: ResearchProject["budget"];
};
const EMPTY: Draft = {
  name: "",
  description: "",
  instructions: "",
  model: "",
  reasoning_effort: "",
  collection_ids: [],
  budget: { sub_queries: 6, top_k_per_query: 5, gap_rounds: 1, max_evidence_chunks: 24 },
};

export default function ResearchProjectPage() {
  const params = useParams<{ pid: string }>();
  const router = useRouter();
  const isNew = params.pid === "new";
  const [project, setProject] = useState<ResearchProject | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [runs, setRuns] = useState<ResearchRunSummary[] | null>(null);
  const [versions, setVersions] = useState<ResearchInstruction[]>([]);
  const [models, setModels] = useState<AnswerModel[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [starting, setStarting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const isAdmin = user?.role === "admin";

  const load = useCallback(async () => {
    if (isNew) return;
    try {
      const [p, r, v] = await Promise.all([
        apiFetch<ResearchProject>(`/api/research/projects/${params.pid}`),
        apiFetch<{ total: number; items: ResearchRunSummary[] }>(`/api/research/projects/${params.pid}/runs?limit=30`),
        apiFetch<ResearchInstruction[]>(`/api/research/projects/${params.pid}/instructions`).catch(() => []),
      ]);
      setProject(p);
      setRuns(r.items);
      setVersions(v);
      setDraft({
        name: p.name,
        description: p.description ?? "",
        instructions: p.instructions,
        model: p.model ?? "",
        reasoning_effort: p.reasoning_effort ?? "",
        collection_ids: p.collection_ids,
        budget: p.budget,
      });
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [isNew, params.pid]);

  useEffect(() => {
    void load();
    apiFetch<User>("/api/auth/me").then(setUser).catch(() => setUser(null));
    apiFetch<AnswerModel[]>("/api/models").then(setModels).catch(() => setModels([]));
    apiFetch<Collection[]>("/api/collections").then(setCollections).catch(() => setCollections([]));
  }, [load]);

  // 진행 중인 실행이 있으면 목록만 3초마다 다시 읽는다.
  useEffect(() => {
    if (!runs?.some((r) => RUNNING.has(r.status))) return;
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [runs, load]);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const body = {
        name: draft.name.trim(),
        description: draft.description.trim() || null,
        model: draft.model || null,
        reasoning_effort: draft.reasoning_effort || null,
        collection_ids: draft.collection_ids,
        budget: draft.budget,
      };
      if (isNew) {
        const created = await apiFetch<ResearchProject>("/api/research/projects", {
          method: "POST",
          body: JSON.stringify({ ...body, instructions: draft.instructions.trim() || null }),
        });
        router.replace(`/research/${created.id}`);
        return;
      }
      await apiFetch(`/api/research/projects/${params.pid}`, { method: "PATCH", body: JSON.stringify(body) });
      // 지침은 버전이다 - 바뀌었을 때만 새 버전을 쌓는다.
      if (project && draft.instructions.trim() !== project.instructions.trim() && draft.instructions.trim()) {
        await apiFetch(`/api/research/projects/${params.pid}/instructions`, {
          method: "POST",
          body: JSON.stringify({ text: draft.instructions.trim() }),
        });
      }
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function activate(version: number) {
    try {
      await apiFetch(`/api/research/projects/${params.pid}/instructions/${version}/activate`, { method: "POST" });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function start(event: React.FormEvent) {
    event.preventDefault();
    if (!prompt.trim()) return;
    setStarting(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("prompt", prompt.trim());
      if (file) form.append("file", file);
      const run = await apiFetch<{ id: string }>(`/api/research/projects/${params.pid}/runs`, { method: "POST", body: form });
      router.push(`/research/runs/${run.id}`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setStarting(false);
    }
  }

  if (!isNew && project === null && !error) {
    return (
      <PageShell>
        <p className="text-body text-on-surface-variant">불러오는 중...</p>
      </PageShell>
    );
  }

  return (
    <PageShell>
      <div className="flex flex-wrap items-center gap-2">
        <Link href="/research" className="icon-btn h-9 w-9" aria-label="딥 리서치 목록으로">
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="m15 6-6 6 6 6" />
          </svg>
        </Link>
        <h1 className="min-w-0 flex-1 truncate text-headline font-medium">{isNew ? "새 리서치 방" : project?.name}</h1>
        {isAdmin && !isNew && (
          <button type="button" onClick={() => setDeleting(true)} className="btn-text btn-compact text-error">
            삭제
          </button>
        )}
      </div>
      <ErrorBanner message={error} />

      {/* 실행 - 사용자가 주로 쓰는 부분이라 맨 위. 새 방은 먼저 저장해야 실행할 수 있다. */}
      {!isNew && (
        <form onSubmit={start} className="space-y-3 rounded-md bg-surface-container-low p-4">
          <h2 className="text-title font-medium">리서치 시작</h2>
          {project?.instructions && (
            <p className="text-caption text-on-surface-variant">이 방의 지침(v{project.instruction_version}): {project.instructions.slice(0, 160)}{project.instructions.length > 160 ? "…" : ""}</p>
          )}
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={4}
            placeholder="무엇을 조사할까요? 대상·조건·궁금한 점을 구체적으로 적을수록 좋습니다."
            className="field w-full"
            aria-label="리서치 요청"
          />
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.txt,.md,.html,.xlsx,.csv"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-caption text-on-surface-variant"
              aria-label="참고 파일 첨부"
            />
            <span className="text-caption text-on-surface-variant">첨부 파일의 글자는 요청과 함께 읽히지만 문서 코퍼스에는 저장되지 않습니다.</span>
            <button type="submit" disabled={starting || !prompt.trim()} className="btn-filled ml-auto">
              {starting ? "시작 중..." : "리서치 시작"}
            </button>
          </div>
        </form>
      )}

      {/* 실행 이력 */}
      {!isNew && runs && runs.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-title font-medium">실행 이력</h2>
          <ul className="divide-y divide-outline-variant rounded-md bg-surface-container-low">
            {runs.map((r) => (
              <li key={r.id}>
                <Link href={`/research/runs/${r.id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 hover:bg-surface-container">
                  <span
                    className={`shrink-0 rounded-full px-2 py-0.5 text-caption ${
                      r.status === "done"
                        ? "bg-primary-container text-on-primary-container"
                        : r.status === "failed"
                          ? "bg-error-container text-on-error-container"
                          : "bg-surface-container-high text-on-surface-variant"
                    }`}
                  >
                    {STATUS_LABEL[r.status] ?? r.status}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-body text-on-surface">{r.prompt}</span>
                  <span className="shrink-0 text-caption text-on-surface-variant">
                    {r.source_count > 0 && `출처 ${r.source_count} · `}
                    {new Date(r.created_at).toLocaleString()}
                    {r.created_by_email && ` · ${r.created_by_email}`}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 방 설정 - 관리자만 편집. 접어 둔다(실행이 주역). */}
      {(isAdmin || isNew) && (
        <details open={isNew} className="group rounded-md bg-surface-container-low">
          <summary className="cursor-pointer list-none px-4 py-3 text-title font-medium [&::-webkit-details-marker]:hidden">
            방 설정 <span className="ml-2 text-caption font-normal text-on-surface-variant group-open:hidden">펼치기 ▾</span>
          </summary>
          <div className="space-y-4 px-4 pb-4">
            <label className="block">
              <span className="text-caption text-on-surface-variant">이름</span>
              <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} maxLength={200} className="field mt-1 w-full" />
            </label>
            <label className="block">
              <span className="text-caption text-on-surface-variant">설명</span>
              <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} maxLength={2000} className="field mt-1 w-full" />
            </label>
            <label className="block">
              <span className="text-caption text-on-surface-variant">
                지침 — 보고서를 “무엇을·어떤 관점으로” 쓸지. 저장할 때마다 새 버전이 되고, 인용 강제 규칙은 지침과 무관하게 항상 붙습니다.
              </span>
              <textarea value={draft.instructions} onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} rows={6} className="field mt-1 w-full" placeholder="비워 두면 범용 조사 보고서 지침을 씁니다." />
            </label>
            {versions.length > 1 && (
              <div className="text-caption text-on-surface-variant">
                지침 이력:{" "}
                {versions.map((v) => (
                  <button key={v.version} type="button" onClick={() => void activate(v.version)} disabled={v.is_active} className={`mr-2 underline ${v.is_active ? "font-medium text-on-surface no-underline" : ""}`}>
                    v{v.version}{v.is_active ? " (현행)" : ""}
                  </button>
                ))}
              </div>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="text-caption text-on-surface-variant">답변 모델 (종합 단계)</span>
                <select value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} className="field mt-1 w-full">
                  <option value="">기본 모델</option>
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                      {m.provider === "local" ? " (로컬 GPU)" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-caption text-on-surface-variant">추론 수준 (추론 모델일 때)</span>
                <select value={draft.reasoning_effort} onChange={(e) => setDraft({ ...draft, reasoning_effort: e.target.value })} className="field mt-1 w-full">
                  {EFFORTS.map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <fieldset>
              <legend className="text-caption text-on-surface-variant">검색 범위 (아무것도 고르지 않으면 전체 분류)</legend>
              <div className="mt-1 flex flex-wrap gap-3">
                {collections.map((c) => {
                  const on = draft.collection_ids.includes(c.id);
                  return (
                    <label key={c.id} className="flex items-center gap-1.5 text-body">
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() =>
                          setDraft({
                            ...draft,
                            collection_ids: on ? draft.collection_ids.filter((id) => id !== c.id) : [...draft.collection_ids, c.id],
                          })
                        }
                      />
                      {c.name}
                    </label>
                  );
                })}
              </div>
            </fieldset>
            <fieldset>
              <legend className="text-caption text-on-surface-variant">검색 예산</legend>
              <div className="mt-1 grid gap-3 sm:grid-cols-2">
                {BUDGET_FIELDS.map((f) => (
                  <label key={f.key} className="block">
                    <span className="flex justify-between text-caption text-on-surface-variant">
                      <span>{f.label}</span>
                      <span className="font-medium text-on-surface">{draft.budget[f.key]}</span>
                    </span>
                    <input
                      type="range"
                      min={f.min}
                      max={f.max}
                      value={draft.budget[f.key]}
                      onChange={(e) => setDraft({ ...draft, budget: { ...draft.budget, [f.key]: Number(e.target.value) } })}
                      className="mt-1 w-full"
                    />
                    <span className="text-caption text-on-surface-variant">{f.help}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="flex justify-end">
              <button type="button" onClick={() => void save()} disabled={saving || !draft.name.trim()} className="btn-filled">
                {saving ? "저장 중..." : isNew ? "만들기" : "저장"}
              </button>
            </div>
          </div>
        </details>
      )}

      {deleting && project && (
        <ConfirmDialog
          title="리서치 방 삭제"
          message={`'${project.name}' 방과 실행 이력 ${project.run_count}건을 지웁니다. 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onConfirm={async () => {
            await apiFetch(`/api/research/projects/${params.pid}`, { method: "DELETE" });
            router.push("/research");
          }}
          onClose={() => setDeleting(false)}
        />
      )}
    </PageShell>
  );
}
