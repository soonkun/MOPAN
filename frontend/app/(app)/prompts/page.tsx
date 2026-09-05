"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import PageShell from "@/components/layout/PageShell";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import DataTable from "@/components/ui/DataTable";
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

  // The 새 프롬프트 form, which agents made necessary: an agent picks a prompt
  // by NAME from this store, and the store had no way to gain a third name.
  const [newName, setNewName] = useState("");
  const [newText, setNewText] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

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

  /** A NEW prompt name, at version 1.
   *
   * Added for agents: an agent picks a prompt from this store, and until this
   * existed the store had only the names the migrations seeded - so an agent
   * could never answer with anything but the deployment's own system prompt,
   * which is the field the whole feature is about. */
  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      await apiFetch<PromptVersion>("/api/prompts", {
        method: "POST",
        body: JSON.stringify({ name: newName, text: newText }),
      });
      setNewName("");
      setNewText("");
      const current = await load(newName);
      if (current) setDraft(current.text);
    } catch (err) {
      setCreateError(errorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  return (
    <PageShell>
      <h1 className="text-center text-headline font-medium md:text-left">프롬프트 관리</h1>
      <ErrorBanner message={loadError} />

      <form onSubmit={handleCreate} className="space-y-3 rounded-md bg-surface-container-low p-6">
        <h2 className="text-title font-medium">새 프롬프트</h2>
        <p className="max-w-measure text-body text-on-surface-variant">
          워크플로우마다 다른 답변 지침을 쓰려면 여기에서 새 프롬프트를 만든 뒤, 워크플로우
          화면에서 선택하세요. 기존 이름은 덮어쓰지 않습니다.
        </p>
        <div className="grid gap-3 sm:grid-cols-3">
          <div>
            <label htmlFor="prompt-new-name" className="text-label font-medium text-on-surface-variant">
              이름
            </label>
            <input
              id="prompt-new-name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              required
              maxLength={100}
              // The server pattern, stated here too so the refusal arrives
              // before the round trip rather than instead of it.
              pattern="[a-z][a-z0-9_]*"
              placeholder="예) field_agent"
              className="field mt-1 w-full font-mono"
            />
            <p className="mt-1 text-caption text-on-surface-variant">
              영문 소문자와 밑줄만 쓸 수 있습니다. 저장 후에는 바꿀 수 없습니다.
            </p>
          </div>
          <div className="sm:col-span-2">
            <label htmlFor="prompt-new-text" className="text-label font-medium text-on-surface-variant">
              내용
            </label>
            <textarea
              id="prompt-new-text"
              value={newText}
              onChange={(e) => setNewText(e.target.value)}
              rows={4}
              spellCheck={false}
              placeholder="예) 너는 현장 담당자를 돕는 조수다. ..."
              className="mt-1 w-full rounded-sm border border-outline bg-surface px-3 py-2 font-mono text-body text-on-surface transition-colors duration-150 focus:border-primary"
            />
          </div>
        </div>
        <ErrorBanner message={createError} />
        <div className="flex justify-end">
          <button type="submit" disabled={creating} className="btn-filled">
            {creating ? "만드는 중..." : "만들기"}
          </button>
        </div>
      </form>

      {prompts === null ? (
        !loadError && (
          <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
        )
      ) : prompts.length === 0 ? (
        <p className="py-8 text-center text-body text-on-surface-variant">프롬프트가 없습니다.</p>
      ) : (
        <DataTable caption="등록된 프롬프트 목록">
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
        </DataTable>
      )}

      {active && (
        <>
          <form onSubmit={handleSave} className="space-y-3 rounded-md bg-surface-container-low p-6">
            <h2 className="text-title font-medium">
              {PROMPT_LABEL[active.name] ?? active.name} 편집
            </h2>

            {/* The one thing an admin has to understand before typing here. It
                is not an ErrorBanner - nothing has gone wrong - so it is a
                .notice: tone, not a rule, per §1 and §4. */}
            <div className="notice">
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
                {/* The coupling this screen used to hide: every word typed here
                    took a token off the evidence, and nothing said so. It no
                    longer does - up to the limit, which is where the refusal on
                    save comes from. */}
                <li>
                  길게 써도 근거 자료에 쓸 토큰 예산은 줄어들지 않습니다. 다만{" "}
                  {active.token_limit.toLocaleString()} 토큰을 넘으면 그때부터는 근거 자료를
                  밀어내기 때문에 저장이 거절됩니다.
                </li>
              </ul>
            </div>

            {/* max-w-measure, not the full column: at 2xl the shell is 1600px
                and an unbounded monospace textarea would set ~190 characters to
                a line, which is not a width anyone edits a prompt at. */}
            <div className="max-w-measure">
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
                {/* Tokens are the unit the limit is actually in, and the browser
                    cannot count them - so this is the SAVED text's cost, from
                    the server, next to the live character count. The refusal on
                    save carries the number for what is in the box. */}
                {" · 저장된 내용 "}
                {active.tokens.toLocaleString()} 토큰 / 최대{" "}
                {active.token_limit.toLocaleString()} 토큰
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
              <DataTable caption={`${PROMPT_LABEL[active.name] ?? active.name}의 버전 기록`}>
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
              </DataTable>
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
    </PageShell>
  );
}
