"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type {
  Agent,
  AnswerModel,
  Collection,
  McpToolOption,
  PromptSummary,
} from "@/lib/types";

/** 에이전트 관리.
 *
 * An agent is a SAVED CONFIGURATION and this screen is the whole of it: a name,
 * a prompt from the store, the collections it may search, the tools it may
 * call, the model that answers, and whether the orchestrator runs. There is no
 * field here that runs code, and there is not meant to be one - the moment an
 * agent needs custom logic it stops being a row and becomes a deployment.
 *
 * THE ONE THING THIS SCREEN MUST NOT GET WRONG is the empty selection. An empty
 * list means UNRESTRICTED, both for collections and for tools, so every empty
 * selection here prints 전체 허용 rather than 없음. An admin who ticks nothing
 * and reads "없음" would believe they had locked an agent down; the server would
 * disagree, and that is precisely the misleading this feature exists not to do.
 *
 * The row shape follows /mcp: an inline expanded editor under the row rather
 * than a modal, because that is what every other admin screen in this app does.
 */

const RISK_LABEL: Record<string, string> = {
  read: "읽기",
  write: "쓰기",
  destructive: "파괴적",
};

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

/** The editable half of an agent, as the form holds it. Separate from `Agent`
 * because the form works in id lists while the response carries objects. */
type Draft = {
  name: string;
  description: string;
  prompt_name: string;
  answer_model: string;
  orchestrator: boolean;
  enabled: boolean;
  collection_ids: string[];
  tool_ids: string[];
};

const EMPTY: Draft = {
  name: "",
  description: "",
  prompt_name: "answer_agent",
  answer_model: "",
  orchestrator: false,
  enabled: true,
  collection_ids: [],
  tool_ids: [],
};

function draftOf(agent: Agent): Draft {
  return {
    name: agent.name,
    description: agent.description ?? "",
    prompt_name: agent.prompt_name,
    answer_model: agent.answer_model ?? "",
    orchestrator: agent.orchestrator,
    enabled: agent.enabled,
    collection_ids: agent.collections.map((c) => c.id),
    tool_ids: agent.tools.map((t) => t.id),
  };
}

/** The wire body. The two lists are ALWAYS sent, even empty: an empty list is a
 * real state (unrestricted), so omitting it - which PATCH reads as "leave
 * alone" - would make clearing a restriction impossible. */
function bodyOf(draft: Draft) {
  return {
    name: draft.name,
    description: draft.description.trim() || null,
    prompt_name: draft.prompt_name,
    answer_model: draft.answer_model || null,
    orchestrator: draft.orchestrator,
    enabled: draft.enabled,
    collection_ids: draft.collection_ids,
    tool_ids: draft.tool_ids,
  };
}

function toggled(list: string[], id: string): string[] {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}

/** One checkbox group. Its empty state is the sentence that keeps this screen
 * honest, which is why it is a parameter and not a shrug. */
function Choices({
  legend,
  help,
  emptyMeans,
  options,
  selected,
  onToggle,
}: {
  legend: string;
  help: string;
  emptyMeans: string;
  options: { id: string; label: string; hint?: string }[];
  selected: string[];
  onToggle: (id: string) => void;
}) {
  return (
    <fieldset className="rounded-sm bg-surface-container p-4">
      <legend className="px-1 text-label font-medium text-on-surface-variant">{legend}</legend>
      <p className="text-caption text-on-surface-variant">{help}</p>
      {options.length === 0 ? (
        <p className="mt-2 text-body text-on-surface-variant">선택할 항목이 없습니다.</p>
      ) : (
        <div className="mt-2 grid gap-1 sm:grid-cols-2">
          {options.map((option) => (
            <label key={option.id} className="flex items-start gap-2 rounded-sm px-1 py-1 text-body">
              <input
                type="checkbox"
                checked={selected.includes(option.id)}
                onChange={() => onToggle(option.id)}
                className="mt-1 h-4 w-4 shrink-0 accent-primary"
              />
              <span className="min-w-0">
                <span className="block truncate text-on-surface">{option.label}</span>
                {option.hint && (
                  <span className="block truncate text-caption text-on-surface-variant">
                    {option.hint}
                  </span>
                )}
              </span>
            </label>
          ))}
        </div>
      )}
      {/* THE SENTENCE. Nothing ticked is "everything allowed", and this is the
          one place an admin can find that out before they rely on it. */}
      <p className="mt-2 text-caption text-primary">
        {selected.length === 0 ? emptyMeans : `${selected.length}개만 허용합니다.`}
      </p>
    </fieldset>
  );
}

export default function AgentsPage() {
  // null is "not loaded yet", not an empty list - the distinction every admin
  // screen here draws so the empty state never flashes. Every endpoint behind
  // this page answers a non-admin with 403 관리자 권한이 필요합니다., which lands
  // in loadError, so there is no client-side role branch.
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [tools, setTools] = useState<McpToolOption[]>([]);
  const [prompts, setPrompts] = useState<PromptSummary[]>([]);
  const [models, setModels] = useState<AnswerModel[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<Draft>(EMPTY);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Agent | null>(null);

  const load = useCallback(async () => {
    try {
      setAgents(await apiFetch<Agent[]>("/api/agents"));
      setLoadError(null);
    } catch (err) {
      setLoadError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
    // Each of the four is what a field on this form offers, and each failure is
    // survivable on its own - a deployment with no MCP server has no tools to
    // list, and that is a normal state, not an error over the whole page.
    void apiFetch<Collection[]>("/api/collections").then(setCollections).catch(() => setCollections([]));
    void apiFetch<McpToolOption[]>("/api/mcp/tools").then(setTools).catch(() => setTools([]));
    void apiFetch<PromptSummary[]>("/api/prompts").then(setPrompts).catch(() => setPrompts([]));
    void apiFetch<AnswerModel[]>("/api/models").then(setModels).catch(() => setModels([]));
  }, [load]);

  const collectionOptions = collections.map((c) => ({
    id: c.id,
    label: c.name,
    hint: c.description ?? undefined,
  }));
  const toolOptions = tools.map((t) => ({
    id: t.id,
    label: `${t.server_name}/${t.name}`,
    hint: `위험도 ${RISK_LABEL[t.risk_level] ?? t.risk_level}`,
  }));

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      await apiFetch<Agent>("/api/agents", { method: "POST", body: JSON.stringify(bodyOf(draft)) });
      setDraft(EMPTY);
      await load();
    } catch (err) {
      setCreateError(errorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  /** Per-row mutation: busy id, row-scoped error, refetch. Errors render beside
   * the control that caused them and are never hoisted to the page banner. */
  async function act(id: string, run: () => Promise<unknown>) {
    setBusyId(id);
    setRowError(null);
    try {
      await run();
      await load();
      return true;
    } catch (err) {
      setRowError({ id, message: errorMessage(err) });
      return false;
    } finally {
      setBusyId(null);
    }
  }

  function editor(value: Draft, onChange: (next: Draft) => void, idPrefix: string) {
    return (
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor={`${idPrefix}-name`} className="text-label font-medium text-on-surface-variant">
              이름
            </label>
            <input
              id={`${idPrefix}-name`}
              value={value.name}
              onChange={(e) => onChange({ ...value, name: e.target.value })}
              required
              maxLength={200}
              placeholder="현장 안전 담당"
              className="field mt-1 w-full"
            />
          </div>
          <div>
            <label
              htmlFor={`${idPrefix}-description`}
              className="text-label font-medium text-on-surface-variant"
            >
              설명
            </label>
            <input
              id={`${idPrefix}-description`}
              value={value.description}
              onChange={(e) => onChange({ ...value, description: e.target.value })}
              maxLength={2000}
              placeholder="사용자가 채팅에서 고를 때 보이는 한 줄 설명"
              className="field mt-1 w-full"
            />
          </div>
          <div>
            <label
              htmlFor={`${idPrefix}-prompt`}
              className="text-label font-medium text-on-surface-variant"
            >
              답변 지침
            </label>
            <select
              id={`${idPrefix}-prompt`}
              value={value.prompt_name}
              onChange={(e) => onChange({ ...value, prompt_name: e.target.value })}
              className="field mt-1 w-full"
            >
              {/* The current value is always an option even if GET /api/prompts
                  failed, or the row would silently reset to the first entry on
                  the next save. */}
              {(prompts.some((p) => p.name === value.prompt_name)
                ? prompts.map((p) => p.name)
                : [value.prompt_name, ...prompts.map((p) => p.name)]
              ).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <p className="mt-1 text-caption text-on-surface-variant">
              프롬프트 관리에서 만든 이름입니다. 내용을 고치면 이 에이전트의 답변도 바로 바뀝니다.
            </p>
          </div>
          <div>
            <label
              htmlFor={`${idPrefix}-model`}
              className="text-label font-medium text-on-surface-variant"
            >
              답변 모델
            </label>
            <select
              id={`${idPrefix}-model`}
              value={value.answer_model}
              onChange={(e) => onChange({ ...value, answer_model: e.target.value })}
              className="field mt-1 w-full"
            >
              <option value="">기본값 사용</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
            <p className="mt-1 text-caption text-on-surface-variant">
              채팅에서 모델을 직접 고르면 그쪽이 우선합니다.
            </p>
          </div>
        </div>

        <Choices
          legend="사용할 분류"
          help="이 에이전트가 검색할 수 있는 문서 분류입니다. 계획을 세울 때도 여기 없는 분류는 쓸 수 없습니다."
          emptyMeans="선택하지 않았으므로 전체 분류를 허용합니다."
          options={collectionOptions}
          selected={value.collection_ids}
          onToggle={(id) => onChange({ ...value, collection_ids: toggled(value.collection_ids, id) })}
        />

        <Choices
          legend="사용할 도구"
          help="이 에이전트가 호출할 수 있는 MCP 도구입니다. 목록에 없는 도구를 지정한 실행 계획은 통째로 거부됩니다."
          emptyMeans="선택하지 않았으므로 전체 도구를 허용합니다."
          options={toolOptions}
          selected={value.tool_ids}
          onToggle={(id) => onChange({ ...value, tool_ids: toggled(value.tool_ids, id) })}
        />

        <div className="flex flex-wrap gap-4">
          <label className="flex items-center gap-2 text-body">
            <input
              type="checkbox"
              checked={value.orchestrator}
              onChange={(e) => onChange({ ...value, orchestrator: e.target.checked })}
              className="h-4 w-4 accent-primary"
            />
            슈퍼 에이전트로 답변
          </label>
          <label className="flex items-center gap-2 text-body">
            <input
              type="checkbox"
              checked={value.enabled}
              onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
              className="h-4 w-4 accent-primary"
            />
            사용
          </label>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
      <h1 className="text-headline font-medium">에이전트 관리</h1>
      <ErrorBanner message={loadError} />

      <form onSubmit={handleCreate} className="space-y-3 rounded-md bg-surface-container-low p-6">
        <h2 className="text-title font-medium">에이전트 등록</h2>
        {/* Nothing has gone wrong, so this is tone rather than a rule: a
            surface-container-high block, never an ErrorBanner. */}
        <div className="rounded-sm bg-surface-container-high p-4 text-body text-on-surface">
          <p className="font-medium">에이전트는 저장된 설정입니다. 코드가 아닙니다.</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-on-surface-variant">
            <li>
              고른 분류와 도구는 권한 경계입니다. 목록 밖의 도구를 지정한 실행 계획은 일부만 걸러
              내는 것이 아니라 통째로 거부되고, 검색은 목록 밖의 분류에 닿지 않습니다.
            </li>
            <li>아무것도 고르지 않으면 전체를 허용한다는 뜻입니다. 제한은 직접 골라야 걸립니다.</li>
            <li>
              에이전트를 하나도 만들지 않으면 지금까지와 똑같이 동작합니다. 채팅에서 고르지 않은
              경우도 마찬가지입니다.
            </li>
          </ul>
        </div>

        {editor(draft, setDraft, "agent-new")}

        <ErrorBanner message={createError} />
        <div className="flex justify-end">
          <button type="submit" disabled={creating} className="btn-filled">
            {creating ? "등록 중..." : "등록"}
          </button>
        </div>
      </form>

      {agents === null ? (
        !loadError && (
          <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
        )
      ) : agents.length === 0 ? (
        <p className="py-8 text-center text-body text-on-surface-variant">
          등록된 에이전트가 없습니다. 채팅은 기본 설정으로 동작합니다.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-sm">
          <table className="w-full text-left text-body">
            <caption className="sr-only">등록된 에이전트 목록</caption>
            <thead>
              <tr className="bg-surface-container-low text-label font-medium text-on-surface-variant">
                <th scope="col" className="px-3 py-3">에이전트</th>
                <th scope="col" className="px-3 py-3">분류</th>
                <th scope="col" className="px-3 py-3">도구</th>
                <th scope="col" className="px-3 py-3">모델</th>
                <th scope="col" className="px-3 py-3">상태</th>
                <th scope="col" className="px-3 py-3">관리</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((agent) => (
                <Fragment key={agent.id}>
                  <tr className="border-b border-outline-variant align-top">
                    <td className="px-3 py-3">
                      <div className="font-medium">{agent.name}</div>
                      <div className="text-caption text-on-surface-variant">
                        {agent.description || "설명 없음"}
                      </div>
                      <div className="text-caption text-on-surface-variant">
                        {agent.prompt_name}
                        {agent.orchestrator && " · 슈퍼 에이전트"}
                      </div>
                    </td>
                    {/* 전체, not 없음. The list is empty because nothing was
                        restricted, and saying 없음 would state the opposite of
                        what the server does. */}
                    <td className="px-3 py-3">
                      {agent.collections.length === 0
                        ? "전체"
                        : agent.collections.map((c) => c.name).join(", ")}
                    </td>
                    <td className="px-3 py-3">
                      {agent.tools.length === 0
                        ? "전체"
                        : agent.tools.map((t) => `${t.server_name}/${t.name}`).join(", ")}
                    </td>
                    <td className="px-3 py-3">{agent.answer_model ?? "기본값"}</td>
                    <td className="px-3 py-3">
                      {agent.enabled ? (
                        <span className="text-primary">사용 중</span>
                      ) : (
                        <span className="text-on-surface-variant">중지</span>
                      )}
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            const open = editing === agent.id;
                            setEditing(open ? null : agent.id);
                            setRowError(null);
                            if (!open) setEditDraft(draftOf(agent));
                          }}
                          aria-expanded={editing === agent.id}
                          className="btn-tonal btn-compact"
                        >
                          {editing === agent.id ? "닫기" : "편집"}
                        </button>
                        <button
                          type="button"
                          disabled={busyId === agent.id}
                          onClick={() =>
                            void act(agent.id, () =>
                              apiFetch(`/api/agents/${agent.id}`, {
                                method: "PATCH",
                                body: JSON.stringify({ enabled: !agent.enabled }),
                              }),
                            )
                          }
                          className="btn-tonal btn-compact"
                        >
                          {agent.enabled ? "중지" : "사용"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setDeleteTarget(agent)}
                          className="btn-danger btn-compact"
                        >
                          삭제
                        </button>
                      </div>
                      {rowError?.id === agent.id && <ErrorBanner message={rowError.message} />}
                      <div className="mt-1 text-caption text-on-surface-variant">
                        {agent.created_by_email ?? "시스템"} · {formatDate(agent.updated_at)}
                      </div>
                    </td>
                  </tr>
                  {editing === agent.id && (
                    <tr className="border-b border-outline-variant">
                      <td colSpan={6} className="bg-surface-container-low px-3 py-4">
                        <form
                          className="space-y-3"
                          onSubmit={async (event) => {
                            event.preventDefault();
                            const ok = await act(agent.id, () =>
                              apiFetch(`/api/agents/${agent.id}`, {
                                method: "PATCH",
                                body: JSON.stringify(bodyOf(editDraft)),
                              }),
                            );
                            // Closed only on success: a refused save has to leave
                            // the admin's typing where they can fix it.
                            if (ok) setEditing(null);
                          }}
                        >
                          {editor(editDraft, setEditDraft, `agent-${agent.id}`)}
                          <div className="flex justify-end gap-2">
                            <button
                              type="button"
                              onClick={() => setEditing(null)}
                              className="btn-text"
                            >
                              취소
                            </button>
                            <button
                              type="submit"
                              disabled={busyId === agent.id}
                              className="btn-filled"
                            >
                              {busyId === agent.id ? "저장 중..." : "저장"}
                            </button>
                          </div>
                        </form>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="에이전트 삭제"
          message={`"${deleteTarget.name}" 에이전트를 삭제합니다. 이 에이전트로 만들어진 지난 답변은 그대로 남고 추적 화면에도 계속 이름이 표시됩니다. 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onConfirm={async () => {
            await apiFetch(`/api/agents/${deleteTarget.id}`, { method: "DELETE" });
            await load();
          }}
          onClose={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
