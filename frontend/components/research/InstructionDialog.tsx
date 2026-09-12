"use client";

import { useEffect, useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { ResearchInstruction, ResearchTemplate } from "@/lib/types";

/** 방의 지침을 한 편의 문서로 보고 고치는 창. 지침은 종합 단계의 시스템 프롬프트 본문이라
 * 한두 줄이 아니라 심사 매뉴얼 분량이다(과제 중복성 검토 템플릿은 12,000자). 방 설정 안의
 * 여섯 줄 textarea로는 읽을 수도 고칠 수도 없어 전용 창으로 뺐다(소유자 지적).
 *
 * 저장할 때마다 새 버전이 쌓이고(append-only), 지난 버전은 '현행으로'로 되돌린다. 템플릿을
 * 고르면 본문을 갈아 끼우되 저장 전까지는 아무것도 바뀌지 않는다. 관리자가 아니면 읽기만. */
export default function InstructionDialog({
  projectId,
  current,
  versions,
  canEdit,
  onSaved,
  onClose,
}: {
  projectId: string;
  current: string;
  versions: ResearchInstruction[];
  canEdit: boolean;
  onSaved: () => Promise<void>;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [text, setText] = useState(current);
  const [templates, setTemplates] = useState<ResearchTemplate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showVersions, setShowVersions] = useState(false);
  const dirty = text.trim() !== current.trim();

  useEffect(() => {
    dialogRef.current?.showModal();
    if (canEdit) apiFetch<ResearchTemplate[]>("/api/research/templates").then(setTemplates).catch(() => setTemplates([]));
  }, [canEdit]);

  async function save() {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/research/projects/${projectId}/instructions`, { method: "POST", body: JSON.stringify({ text: text.trim() }) });
      await onSaved();
      dialogRef.current?.close();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function activate(version: number) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/research/projects/${projectId}/instructions/${version}/activate`, { method: "POST" });
      await onSaved();
      dialogRef.current?.close();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function applyTemplate(id: string) {
    const t = templates.find((x) => x.id === id);
    if (!t) return;
    if (dirty && !window.confirm("고치던 내용을 버리고 템플릿으로 바꿀까요?")) return;
    setText(t.instructions);
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="instruction-title"
      onClose={onClose}
      // 문서를 다루는 창이라 화면을 거의 다 쓴다. 높이를 고정해야 textarea가 스크롤을 갖는다.
      className="motion-pop h-[92dvh] w-[min(64rem,96vw)] max-w-none rounded-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim"
    >
      <div className="flex h-full flex-col p-4 sm:p-6">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="instruction-title" className="min-w-0 flex-1 text-title font-medium">
            이 방의 지침
          </h2>
          {canEdit && templates.length > 0 && (
            <label className="flex items-center gap-2 text-caption text-on-surface-variant">
              템플릿으로 바꾸기
              <select defaultValue="" onChange={(e) => { applyTemplate(e.target.value); e.target.value = ""; }} className="field h-8 text-body">
                <option value="" disabled>
                  고르기…
                </option>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} ({t.instructions.length.toLocaleString()}자)
                  </option>
                ))}
              </select>
            </label>
          )}
          {versions.length > 1 && (
            <button type="button" onClick={() => setShowVersions((v) => !v)} className="btn-text btn-compact">
              이력 {versions.length}개
            </button>
          )}
        </div>
        <p className="mt-1 text-caption text-on-surface-variant">
          보고서를 “무엇을·어떤 관점으로·어떤 구성으로” 쓸지 정하는 문서입니다. 종합 단계의 시스템 프롬프트가 되며, 근거 인용
          강제와 출력 형식 규칙은 이 지침과 무관하게 항상 뒤에 붙습니다. 저장할 때마다 새 버전이 됩니다.
        </p>
        {showVersions && (
          <ul className="mt-2 max-h-40 divide-y divide-outline-variant overflow-y-auto rounded-md bg-surface-container text-body">
            {[...versions].reverse().map((v) => (
              <li key={v.version} className="flex items-center gap-3 px-3 py-2">
                <span className="w-10 shrink-0 font-medium">v{v.version}</span>
                <span className="min-w-0 flex-1 truncate text-on-surface-variant">
                  {new Date(v.created_at).toLocaleString("ko-KR")}
                  {v.created_by_email && ` · ${v.created_by_email.split("@")[0]}`} · {v.text.length.toLocaleString()}자
                </span>
                {v.is_active ? (
                  <span className="text-caption text-primary">현행</span>
                ) : (
                  <>
                    <button type="button" onClick={() => setText(v.text)} className="btn-text btn-compact">
                      불러오기
                    </button>
                    {canEdit && (
                      <button type="button" onClick={() => void activate(v.version)} disabled={busy} className="btn-tonal btn-compact">
                        현행으로
                      </button>
                    )}
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          readOnly={!canEdit}
          spellCheck={false}
          aria-label="지침 본문"
          className="field mt-3 min-h-0 flex-1 w-full resize-none font-mono text-[13px] leading-relaxed"
          placeholder="비워 두면 범용 조사 보고서 지침을 씁니다."
        />
        <div className="mt-2">
          <ErrorBanner message={error} />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-caption text-on-surface-variant">
            {text.length.toLocaleString()}자 / 20,000자{dirty && " · 저장 전"}
          </span>
          <span className="flex-1" />
          <button type="button" onClick={() => dialogRef.current?.close()} className="btn-text">
            {canEdit ? "취소" : "닫기"}
          </button>
          {canEdit && (
            <button type="button" onClick={() => void save()} disabled={busy || !dirty || !text.trim() || text.length > 20000} className="btn-filled">
              {busy ? "저장 중..." : "새 버전으로 저장"}
            </button>
          )}
        </div>
      </div>
    </dialog>
  );
}
