"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { PromptSummary, PromptVersion } from "@/lib/types";

// The stored key is what get_prompt() looks up and what Message.prompt_name
// records; it is not a thing to put in front of an admin on its own. 답변 지침 is
// the owner's own word for it. An unmapped name falls back to the key rather
// than to a blank, so a prompt added later is still identifiable.
const PROMPT_LABEL: Record<string, string> = {
  answer_agent: "답변 지침",
};

const SEED_AUTHOR = "시스템";

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

export default function PromptsPage() {
  // null is "not loaded yet", which is not the same as an empty list - the same
  // distinction the 분류 and 사용자 screens draw. GET /api/prompts answers a
  // non-admin with 403 관리자 권한이 필요합니다., which lands in loadError, so
  // this page needs no role branch of its own.
  const [prompts, setPrompts] = useState<PromptSummary[] | null>(null);
  const [versions, setVersions] = useState<PromptVersion[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  // The textarea is uncontrolled by the server: once the admin has typed, a
  // background reload must not overwrite what is under the cursor. `draft` is
  // reset only when the selected prompt changes or a save succeeds.
  const [draft, setDraft] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const [expanded, setExpanded] = useState<string | null>(null);
  const [activateTarget, setActivateTarget] = useState<PromptVersion | null>(null);

  const active = prompts?.find((p) => p.name === selected) ?? null;
  const dirty = active !== null && draft !== active.text;

  const load = useCallback(async (name: string | null) => {
    try {
      const list = await apiFetch<PromptSummary[]>("/api/prompts");
      setPrompts(list);
      setLoadError(null);
      // One prompt today, so there is nothing to choose: selecting it is what
      // makes the screen show an editor instead of a one-row table.
      const target = name ?? list[0]?.name ?? null;
      setSelected(target);
      if (target) {
        setVersions(await apiFetch<PromptVersion[]>(`/api/prompts/${target}/versions`));
      }
      return list.find((p) => p.name === target) ?? null;
    } catch (err) {
      setLoadError(errorMessage(err));
      return null;
    }
  }, []);

  useEffect(() => {
    void load(null).then((current) => {
      if (current) setDraft(current.text);
    });
  }, [load]);

  async function handleSave(event: React.FormEvent) {
    event.preventDefault();
    if (!selected) return;
    setSaving(true);
    setSaveError(null);
    setSaved(null);
    try {
      // The server, not this form, is what refuses a blank template - so the
      // button stays enabled on an empty textarea and the Korean 400
      // 프롬프트 내용을 입력해 주세요... renders below it. A disabled button would
      // hide the guard rather than exercise it.
      const created = await apiFetch<PromptVersion>(`/api/prompts/${selected}/versions`, {
        method: "POST",
        body: JSON.stringify({ text: draft }),
      });
      const current = await load(selected);
      if (current) setDraft(current.text);
      setSaved(created.version);
    } catch (err) {
      setSaveError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function activate(version: PromptVersion) {
    await apiFetch(`/api/prompts/${selected}/versions/${version.version}/activate`, {
      method: "POST",
    });
    const current = await load(selected);
    // The editor follows the activation: leaving the previous draft in the box
    // over a rolled-back prompt is how an admin re-saves the text they just
    // rejected.
    if (current) setDraft(current.text);
    setSaved(null);
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
      <h1 className="text-headline font-medium">프롬프트 관리</h1>
      <ErrorBanner message={loadError} />

      {prompts === null ? (
        !loadError && (
          <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
        )
      ) : prompts.length === 0 ? (
        <p className="py-8 text-center text-body text-on-surface-variant">프롬프트가 없습니다.</p>
      ) : (
        <div className="overflow-x-auto rounded-sm">
          <table className="w-full text-left text-body">
            <caption className="sr-only">등록된 프롬프트 목록</caption>
            <thead>
              <tr className="bg-surface-container-low text-label font-medium text-on-surface-variant">
                <th scope="col" className="px-3 py-3">프롬프트</th>
                <th scope="col" className="px-3 py-3">사용 중인 버전</th>
                <th scope="col" className="px-3 py-3">버전 수</th>
                <th scope="col" className="px-3 py-3">등록일</th>
              </tr>
            </thead>
            <tbody>
              {prompts.map((p) => (
                <tr
                  key={p.name}
                  className={`border-b border-outline-variant ${
                    p.name === selected ? "bg-primary-container" : ""
                  }`}
                >
                  <td className="px-3 py-3">
                    {/* A button, not a row click handler: the row is the thing
                        being chosen and it has to be reachable by Tab. */}
                    <button
                      type="button"
                      aria-pressed={p.name === selected}
                      onClick={() => {
                        setSelected(p.name);
                        setDraft(p.text);
                        setSaved(null);
                        setSaveError(null);
                        setExpanded(null);
                        void apiFetch<PromptVersion[]>(`/api/prompts/${p.name}/versions`)
                          .then(setVersions)
                          .catch((err) => setLoadError(errorMessage(err)));
                      }}
                      className={`text-label font-medium underline ${
                        p.name === selected ? "text-on-primary-container" : "text-primary"
                      }`}
                    >
                      {PROMPT_LABEL[p.name] ?? p.name}
                    </button>
                    <div
                      className={`text-caption ${
                        p.name === selected ? "text-on-primary-container" : "text-on-surface-variant"
                      }`}
                    >
                      {p.name}
                    </div>
                  </td>
                  <td className="px-3 py-3">v{p.version}</td>
                  <td className="px-3 py-3">{p.version_count}개</td>
                  <td className="px-3 py-3">{formatDate(p.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {active && (
        <>
          <form onSubmit={handleSave} className="space-y-3 rounded-md bg-surface-container-low p-6">
            <h2 className="text-title font-medium">
              {PROMPT_LABEL[active.name] ?? active.name} 편집
            </h2>

            {/* The one thing an admin has to understand before typing here. It
                is not an ErrorBanner - nothing has gone wrong - so it is a
                surface-container-high block, per §1 and §4: tone, not a rule. */}
            <div className="rounded-sm bg-surface-container-high p-4 text-body text-on-surface">
              <p className="font-medium">이 내용은 모든 질문에서 모델에게 그대로 전달됩니다.</p>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-on-surface-variant">
                <li>
                  저장하면 새 버전이 만들어지고 바로 적용됩니다. 다음 질문부터 즉시 반영되며, 다시
                  배포할 필요는 없습니다.
                </li>
                <li>
                  인용 표기 규칙과 프롬프트 주입 대응 지침이 이 안에 들어 있습니다. 지우면 답변
                  품질이 그만큼 떨어집니다.
                </li>
                <li>
                  근거 자료를 감싸는 보안 울타리는 코드에서 만들어지므로, 이 내용을 어떻게 고쳐도
                  사라지지 않습니다.
                </li>
              </ul>
            </div>

            <div>
              <label htmlFor="prompt-text" className="text-label font-medium text-on-surface-variant">
                프롬프트 내용
              </label>
              <textarea
                id="prompt-text"
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setSaved(null);
                }}
                rows={18}
                spellCheck={false}
                aria-describedby="prompt-text-help"
                // Not `.field`: that class fixes a 40px height for a one-line
                // input. Same resting outline and focus token, own height.
                className="mt-1 w-full rounded-sm border border-outline bg-surface px-3 py-2 font-mono text-body text-on-surface transition-colors duration-150 focus:border-primary"
              />
              <p id="prompt-text-help" className="mt-1 text-caption text-on-surface-variant">
                현재 사용 중인 버전 v{active.version} · {draft.length.toLocaleString()}자
                {dirty && " · 저장하지 않은 변경이 있습니다"}
              </p>
            </div>

            <ErrorBanner message={saveError} />
            {saved && (
              <p className="text-body text-primary" role="status">
                v{saved} 버전으로 저장했습니다. 다음 질문부터 적용됩니다.
              </p>
            )}

            <div className="flex justify-end gap-2">
              <button
                type="button"
                disabled={!dirty || saving}
                onClick={() => {
                  setDraft(active.text);
                  setSaveError(null);
                  setSaved(null);
                }}
                className="btn-text"
              >
                되돌리기
              </button>
              <button type="submit" disabled={!dirty || saving} className="btn-filled">
                {saving ? "저장 중..." : "새 버전으로 저장"}
              </button>
            </div>
          </form>

          <section className="space-y-3">
            <h2 className="text-title font-medium">버전 기록</h2>
            {versions === null ? (
              <p className="text-body text-on-surface-variant">불러오는 중...</p>
            ) : (
              <div className="overflow-x-auto rounded-sm">
                <table className="w-full text-left text-body">
                  <caption className="sr-only">
                    {PROMPT_LABEL[active.name] ?? active.name}의 버전 기록
                  </caption>
                  <thead>
                    <tr className="bg-surface-container-low text-label font-medium text-on-surface-variant">
                      <th scope="col" className="px-3 py-3">버전</th>
                      <th scope="col" className="px-3 py-3">상태</th>
                      <th scope="col" className="px-3 py-3">등록자</th>
                      <th scope="col" className="px-3 py-3">등록일</th>
                      <th scope="col" className="px-3 py-3">관리</th>
                    </tr>
                  </thead>
                  <tbody>
                    {versions.map((v) => (
                      // Two rows per version: the summary, and the text when it
                      // is expanded. A keyed <Fragment>, not <>, because the two
                      // <tr>s are siblings in a list - the shorthand takes no key.
                      // A fragment rather than nesting the <pre> inside a cell, so
                      // the preview spans the full width instead of squeezing into
                      // the 관리 column.
                      <Fragment key={v.id}>
                        <tr className="border-b border-outline-variant align-top">
                          <td className="px-3 py-3">v{v.version}</td>
                          <td className="px-3 py-3">
                            {v.is_active ? (
                              <span className="text-primary">사용 중</span>
                            ) : (
                              <span className="text-on-surface-variant">보관</span>
                            )}
                          </td>
                          {/* The seeded version predates every account, so it
                              has no 등록자 to name. */}
                          <td className="px-3 py-3">{v.created_by_email ?? SEED_AUTHOR}</td>
                          <td className="px-3 py-3 text-on-surface-variant">
                            {formatDate(v.created_at)}
                          </td>
                          <td className="px-3 py-3">
                            <div className="flex gap-2">
                              <button
                                type="button"
                                aria-expanded={expanded === v.id}
                                aria-controls={`prompt-version-${v.id}`}
                                onClick={() => setExpanded(expanded === v.id ? null : v.id)}
                                className="btn-tonal btn-compact"
                              >
                                {expanded === v.id ? `v${v.version} 접기` : `v${v.version} 보기`}
                              </button>
                              {!v.is_active && (
                                <button
                                  type="button"
                                  onClick={() => setActivateTarget(v)}
                                  className="btn-tonal btn-compact"
                                >
                                  v{v.version} 사용하기
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                        {expanded === v.id && (
                          <tr className="border-b border-outline-variant">
                            <td colSpan={5} className="px-3 pb-4">
                              <pre
                                id={`prompt-version-${v.id}`}
                                tabIndex={0}
                                aria-label={`v${v.version} 전문`}
                                className="max-h-96 overflow-auto whitespace-pre-wrap rounded-sm bg-surface-container-high p-4 font-mono text-body"
                              >
                                {v.text}
                              </pre>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      {activateTarget && (
        <ConfirmDialog
          title="이전 버전 사용"
          message={`v${activateTarget.version}을(를) 사용 중인 버전으로 바꿀까요? 다음 질문부터 이 내용이 모델에 전달됩니다. 지금 사용 중인 버전은 지워지지 않고 기록에 남습니다.`}
          confirmLabel="사용하기"
          onConfirm={() => activate(activateTarget)}
          onClose={() => setActivateTarget(null)}
        />
      )}
    </div>
  );
}
