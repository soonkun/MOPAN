"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { apiFetch, errorMessage } from "@/lib/api";
import InstructionDialog from "@/components/research/InstructionDialog";
import PageShell from "@/components/layout/PageShell";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type {
  AnswerModel,
  Collection,
  ResearchInstruction,
  ResearchProject,
  ResearchRunSummary,
  ResearchTemplate,
  User,
} from "@/lib/types";

/** 딥 리서치 방. 흐름은 위에서 아래로 "검토 시작 → 실행 이력 → 방 설정"이고, 지침은 전용 창
 * (InstructionDialog)에서 한 편의 문서로 본다. 새 방은 템플릿(중복성 검토·신규과제 발굴·계획서
 * 초안)을 먼저 고른다 - 지침을 빈 칸에서 쓰는 사람은 없다. 용어는 셋뿐이다: 방·지침·리서치(실행). */

const STATUS_LABEL: Record<string, string> = {
  queued: "대기",
  planning: "계획",
  searching: "검색",
  gap: "보완 검색",
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
  { key: "sub_queries", label: "검색 관점 수", min: 1, max: 12, help: "요청을 몇 갈래의 검색 질의로 나눌지" },
  { key: "top_k_per_query", label: "관점당 근거 수", min: 1, max: 15, help: "질의 하나가 가져오는 문서 조각 수" },
  { key: "gap_rounds", label: "보완 검색 횟수", min: 0, max: 3, help: "빈 관점을 찾아 다시 검색하는 횟수. 0이면 생략" },
  { key: "max_evidence_chunks", label: "보고서 근거 상한", min: 5, max: 80, help: "보고서 작성에 넘기는 최대 근거 수" },
];

type Draft = {
  name: string;
  description: string;
  planner_hint: string;
  model: string;
  reasoning_effort: string;
  collection_ids: string[];
  budget: ResearchProject["budget"];
};
const EMPTY: Draft = {
  name: "",
  description: "",
  planner_hint: "",
  model: "",
  reasoning_effort: "",
  collection_ids: [],
  budget: { sub_queries: 6, top_k_per_query: 5, gap_rounds: 1, max_evidence_chunks: 24 },
};

function firstLine(text: string): string {
  const line = text.split("\n").map((l) => l.replace(/^#+\s*/, "").trim()).find(Boolean) ?? "";
  return line.length > 90 ? `${line.slice(0, 90)}…` : line;
}

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
  const [templates, setTemplates] = useState<ResearchTemplate[]>([]);
  const [templateId, setTemplateId] = useState<string>("duplication");
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [starting, setStarting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [editingInstructions, setEditingInstructions] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const isAdmin = user?.role === "admin";
  const template = templates.find((t) => t.id === templateId) ?? null;
  const isDuplication = project?.template_id === "duplication";

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
        planner_hint: p.planner_hint ?? "",
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
    if (isNew) apiFetch<ResearchTemplate[]>("/api/research/templates").then(setTemplates).catch(() => setTemplates([]));
  }, [load, isNew]);

  // 템플릿을 고르면 이름·설명·관점을 미리 채운다(빈 칸에서 시작하지 않게). 사용자가 고친 값은 안 건드린다.
  useEffect(() => {
    if (!isNew || !template) return;
    setDraft((d) => ({
      ...d,
      name: d.name || template.name,
      description: d.description || template.description,
      planner_hint: d.planner_hint || template.planner_hint,
    }));
  }, [isNew, template]);

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
        planner_hint: draft.planner_hint.trim() || null,
        model: draft.model || null,
        reasoning_effort: draft.reasoning_effort || null,
        collection_ids: draft.collection_ids,
        budget: draft.budget,
      };
      if (isNew) {
        const created = await apiFetch<ResearchProject>("/api/research/projects", {
          method: "POST",
          body: JSON.stringify({ ...body, template_id: templateId === "blank" ? null : templateId }),
        });
        router.replace(`/research/${created.id}`);
        return;
      }
      await apiFetch(`/api/research/projects/${params.pid}`, { method: "PATCH", body: JSON.stringify(body) });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function start(event: React.FormEvent) {
    event.preventDefault();
    if (!prompt.trim() && !file) return;
    setStarting(true);
    setError(null);
    try {
      const form = new FormData();
      // 첨부만 있고 요청문이 비면 방의 목적을 요청문으로 쓴다 - 중복성 검토는 파일이 곧 요청이다.
      form.append("prompt", prompt.trim() || (isDuplication ? "첨부한 과제 계획서의 중복성을 검토하고 개선방향을 제시해 주세요." : `첨부 문서를 검토해 주세요.`));
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

  const running = runs?.some((r) => RUNNING.has(r.status)) ?? false;

  return (
    <PageShell>
      {/* 머리 줄 규칙은 PageHeader와 같다(햄버거 자리 pl-12, 높이 min-h-10). */}
      <div className="flex min-h-10 items-center gap-2 pl-12 md:pl-0">
        <Link href="/research" className="icon-btn h-9 w-9 shrink-0" aria-label="딥 리서치 목록으로">
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="m15 6-6 6 6 6" />
          </svg>
        </Link>
        <h1 className="min-w-0 flex-1 truncate text-headline font-medium">{isNew ? "새 리서치 방" : project?.name}</h1>
        {isAdmin && !isNew && (
          <button type="button" onClick={() => setDeleting(true)} className="btn-text btn-compact text-error">
            방 삭제
          </button>
        )}
      </div>
      <ErrorBanner message={error} />

      {/* 새 방: 템플릿부터 고른다. */}
      {isNew && (
        <section className="space-y-3">
          <h2 className="text-title font-medium">어떤 일을 하는 방인가요?</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {[...templates, { id: "blank", name: "빈 방", description: "지침을 직접 씁니다. 비워 두면 범용 조사 보고서 지침을 씁니다.", planner_hint: "", instructions: "" }].map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTemplateId(t.id)}
                aria-pressed={templateId === t.id}
                className={`rounded-md p-4 text-left transition-colors duration-150 ${
                  templateId === t.id ? "bg-primary-container text-on-primary-container" : "bg-surface-container-low hover:bg-surface-container"
                }`}
              >
                <span className="block text-title font-medium">{t.name}</span>
                <span className="mt-1 block text-body opacity-90">{t.description}</span>
                {t.instructions && <span className="mt-2 block text-caption opacity-75">지침 {t.instructions.length.toLocaleString()}자 · 만든 뒤 고칠 수 있습니다</span>}
              </button>
            ))}
          </div>
        </section>
      )}

      {/* 검토 시작 - 사용자가 주로 쓰는 부분이라 맨 위. */}
      {!isNew && project && (
        <form onSubmit={start} className="space-y-3 rounded-md bg-surface-container-low p-4">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="min-w-0 flex-1 text-title font-medium">{isDuplication ? "중복성 검토 시작" : "리서치 시작"}</h2>
            {running && <span className="text-caption text-on-surface-variant">진행 중인 리서치가 끝나야 새로 시작할 수 있습니다</span>}
          </div>
          {project.description && <p className="text-body text-on-surface-variant">{project.description}</p>}

          {/* 지침 - 한 줄 요약 + 전용 창. 여기서 본문을 펼치지 않는다. */}
          <div className="flex flex-wrap items-center gap-2 rounded-md bg-surface-container px-3 py-2 text-body">
            <span className="shrink-0 font-medium text-on-surface">지침 v{project.instruction_version || 0}</span>
            <span className="min-w-0 flex-1 truncate text-on-surface-variant">
              {project.instructions ? `${firstLine(project.instructions)} · ${project.instructions.length.toLocaleString()}자` : "지침 없음 - 범용 조사 보고서 형식으로 씁니다"}
            </span>
            <button type="button" onClick={() => setEditingInstructions(true)} className="btn-tonal btn-compact shrink-0">
              {isAdmin ? "지침 보기·편집" : "지침 보기"}
            </button>
          </div>

          {/* 첨부가 주역인 방(중복성 검토)은 파일을 먼저, 질문형 방은 요청문을 먼저. */}
          <div className={isDuplication ? "space-y-3" : "flex flex-col-reverse gap-3"}>
            <div className="rounded-md border border-dashed border-outline-variant p-3">
              <label className="flex flex-wrap items-center gap-2 text-body text-on-surface">
                <span className="btn-tonal btn-compact cursor-pointer">{isDuplication ? "신규 과제 계획서 첨부" : "참고 파일 첨부"}</span>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".pdf,.docx,.txt,.md,.html,.xlsx,.csv"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  className="sr-only"
                  aria-label="파일 첨부"
                />
                <span className="min-w-0 truncate text-on-surface-variant">{file ? file.name : "선택한 파일 없음"}</span>
                {file && (
                  <button type="button" onClick={() => { setFile(null); if (fileRef.current) fileRef.current.value = ""; }} className="btn-text btn-compact">
                    제거
                  </button>
                )}
              </label>
              <p className="mt-1 text-caption text-on-surface-variant">
                {isDuplication
                  ? "계획서(PDF·DOCX·HWP 변환본 등)를 붙이면 먼저 목표·대상·방법·산출물을 읽어 검토 대상을 정리하고, 그 관점마다 등록된 보고서·계획서를 찾아 비교합니다. 파일은 코퍼스에 저장되지 않습니다."
                  : "첨부 파일의 글자는 요청과 함께 읽히지만 문서 코퍼스에는 저장되지 않습니다."}
              </p>
            </div>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={isDuplication ? 3 : 5}
              placeholder={
                isDuplication
                  ? "(선택) 특별히 봐야 할 점이 있으면 적어 주세요. 예: 2020~2024년 완결보고서와 비교, 데이터 구축 부분의 중복 여부 중점"
                  : "무엇을 조사할까요? 대상·조건·궁금한 점을 구체적으로 적을수록 좋습니다."
              }
              className="field w-full text-body"
              aria-label="리서치 요청"
            />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-caption text-on-surface-variant">
              {project.budget.sub_queries}개 관점으로 검색 · 보완 {project.budget.gap_rounds}회 · 근거 최대 {project.budget.max_evidence_chunks}건 · 몇 분 걸리며 화면을 떠나도 계속됩니다
            </span>
            <button type="submit" disabled={starting || running || (!prompt.trim() && !file)} className="btn-filled">
              {starting ? "시작 중..." : isDuplication ? "중복성 검토 시작" : "리서치 시작"}
            </button>
          </div>
        </form>
      )}

      {/* 실행 이력 */}
      {!isNew && runs && runs.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-title font-medium">보고서</h2>
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
                          : RUNNING.has(r.status)
                            ? "bg-surface-container-high text-on-surface-variant animate-pulse"
                            : "bg-surface-container-high text-on-surface-variant"
                    }`}
                  >
                    {STATUS_LABEL[r.status] ?? r.status}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-body text-on-surface">{r.prompt}</span>
                  <span className="w-full text-caption text-on-surface-variant sm:w-auto sm:shrink-0">
                    {r.source_count > 0 && `출처 ${r.source_count} · `}
                    {new Date(r.created_at).toLocaleDateString()}
                    {r.created_by_email && ` · ${r.created_by_email.split("@")[0]}`}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 방 설정 - 관리자만 편집. 지침은 위의 전용 창에서. */}
      {(isAdmin || isNew) && (
        <details open={isNew} className="group rounded-md bg-surface-container-low">
          <summary className="cursor-pointer list-none px-4 py-3 text-title font-medium [&::-webkit-details-marker]:hidden">
            {isNew ? "방 이름과 설정" : "방 설정"}
            {!isNew && <span className="ml-2 text-caption font-normal text-on-surface-variant group-open:hidden">펼치기 ▾</span>}
          </summary>
          <div className="space-y-4 px-4 pb-4">
            <label className="block">
              <span className="text-caption text-on-surface-variant">이름</span>
              <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} maxLength={200} className="field mt-1 w-full" />
            </label>
            <label className="block">
              <span className="text-caption text-on-surface-variant">설명 - 검토 시작 카드에 보이는 한 줄</span>
              <input value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} maxLength={2000} className="field mt-1 w-full" />
            </label>
            <label className="block">
              <span className="text-caption text-on-surface-variant">
                검색 관점 힌트 - 요청을 검색 질의로 나눌 때 어느 축으로 나눌지. 예: “핵심 주제어, 유사 기술/방법, 같은 대상, 선행 사업명, 산출물”
              </span>
              <input value={draft.planner_hint} onChange={(e) => setDraft({ ...draft, planner_hint: e.target.value })} maxLength={1000} className="field mt-1 w-full" />
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="text-caption text-on-surface-variant">보고서 작성 모델</span>
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
                {saving ? "저장 중..." : isNew ? "방 만들기" : "저장"}
              </button>
            </div>
          </div>
        </details>
      )}

      {editingInstructions && project && (
        <InstructionDialog
          projectId={project.id}
          current={project.instructions}
          versions={versions}
          canEdit={isAdmin}
          onSaved={load}
          onClose={() => setEditingInstructions(false)}
        />
      )}

      {deleting && project && (
        <ConfirmDialog
          title="리서치 방 삭제"
          message={`'${project.name}' 방과 보고서 ${project.run_count}건을 지웁니다. 되돌릴 수 없습니다.`}
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
