"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import AgentCanvas, { type Catalog, type Draft } from "@/components/agents/AgentCanvas";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type {
  Agent,
  AnswerModel,
  Collection,
  McpToolOption,
  PromptSummary,
} from "@/lib/types";

/** 에이전트 생성 — a builder, not a form.
 *
 * The screen this replaces was a name field, two dropdowns and two checkbox
 * lists. Everything it saved, this saves: the SAME `agents` object, the SAME
 * POST/PATCH/DELETE, the same two join tables. Nothing was added to the schema
 * and nothing was migrated - the canvas is a second way of looking at one row.
 *
 * Why it was worth rewriting: the thing that matters about an agent is its
 * BOUNDARY, and a checkbox list is the worst possible way to show one. Two
 * ticks in a list of twenty is visually indistinguishable from twenty ticks.
 * Two cards on a canvas beside an empty tool group is not.
 *
 * The list and the builder live on one page. Selecting an agent loads it into
 * the canvas; 새 에이전트 clears it. Leaving a dirty draft is confirmed rather
 * than silently dropped - the canvas is a lot of clicks to lose to a misaimed
 * one.
 */

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

/** The wire body. Both lists are ALWAYS sent, even empty: an empty list is a
 * real state (unrestricted), and PATCH reads an omitted key as "leave alone",
 * so omitting them would make clearing a restriction impossible. */
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

/** 전체, not 없음. An empty list is unrestricted, and this is the summary an
 * admin reads down the list without opening anything. */
function summary(agent: Agent): string {
  const collections =
    agent.collections.length === 0 ? "전체 분류" : `분류 ${agent.collections.length}개`;
  const tools = agent.tools.length === 0 ? "전체 도구" : `도구 ${agent.tools.length}개`;
  return `${collections} · ${tools}${agent.orchestrator ? " · 슈퍼" : ""}`;
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

  // null = building a new agent. A string = editing that saved one.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [baseline, setBaseline] = useState<Draft>(EMPTY);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [pendingSelect, setPendingSelect] = useState<{ id: string | null } | null>(null);
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
    // Each of the four is what a module on the canvas offers, and each failure
    // is survivable on its own - a deployment with no MCP server has no tools
    // to place, and that is a normal state, not an error over the whole page.
    void apiFetch<Collection[]>("/api/collections").then(setCollections).catch(() => setCollections([]));
    void apiFetch<McpToolOption[]>("/api/mcp/tools").then(setTools).catch(() => setTools([]));
    void apiFetch<PromptSummary[]>("/api/prompts").then(setPrompts).catch(() => setPrompts([]));
    void apiFetch<AnswerModel[]>("/api/models").then(setModels).catch(() => setModels([]));
  }, [load]);

  const catalog: Catalog = useMemo(
    () => ({ collections, tools, prompts, models }),
    [collections, tools, prompts, models],
  );

  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline);

  function open(id: string | null) {
    const agent = id === null ? null : agents?.find((a) => a.id === id);
    const next = agent ? draftOf(agent) : EMPTY;
    setEditingId(id);
    setDraft(next);
    setBaseline(next);
    setSaveError(null);
  }

  function select(id: string | null) {
    if (dirty) setPendingSelect({ id });
    else open(id);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setSaveError(null);
    try {
      const saved = editingId
        ? await apiFetch<Agent>(`/api/agents/${editingId}`, {
            method: "PATCH",
            body: JSON.stringify(bodyOf(draft)),
          })
        : await apiFetch<Agent>("/api/agents", {
            method: "POST",
            body: JSON.stringify(bodyOf(draft)),
          });
      await load();
      // Stay on what was just saved rather than resetting to a blank canvas: a
      // save is usually the middle of the work, not the end of it.
      setEditingId(saved.id);
      setBaseline(draftOf(saved));
      setDraft(draftOf(saved));
    } catch (err) {
      setSaveError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6">
      <h1 className="text-headline font-medium">에이전트 생성</h1>
      <ErrorBanner message={loadError} />

      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)] lg:items-start">
        <section
          aria-labelledby="saved-agents"
          className="rounded-md bg-surface-container-low p-4"
        >
          <div className="flex items-center justify-between gap-2">
            <h2 id="saved-agents" className="text-title font-medium">
              저장된 에이전트
            </h2>
            <button type="button" onClick={() => select(null)} className="btn-tonal btn-compact">
              새로 만들기
            </button>
          </div>

          {agents === null ? (
            !loadError && <p className="mt-4 text-body text-on-surface-variant">불러오는 중...</p>
          ) : agents.length === 0 ? (
            <p className="mt-4 text-body text-on-surface-variant">
              저장된 에이전트가 없습니다. 하나도 만들지 않으면 채팅은 지금까지와 똑같이 동작합니다.
            </p>
          ) : (
            <ul className="mt-4 space-y-1">
              {agents.map((agent) => {
                const active = editingId === agent.id;
                return (
                  <li key={agent.id}>
                    <button
                      type="button"
                      onClick={() => select(agent.id)}
                      aria-current={active ? "true" : undefined}
                      className={`w-full rounded-md px-3 py-2 text-left transition-colors duration-150 ${
                        active
                          ? "bg-primary-container text-on-primary-container"
                          : "text-on-surface hover:bg-surface-container"
                      }`}
                    >
                      <span className="block truncate text-body font-medium">{agent.name}</span>
                      <span
                        className={`block truncate text-caption ${
                          active ? "text-on-primary-container" : "text-on-surface-variant"
                        }`}
                      >
                        {summary(agent)}
                      </span>
                      {!agent.enabled && (
                        <span
                          className={`block text-caption ${
                            active ? "text-on-primary-container" : "text-on-surface-variant"
                          }`}
                        >
                          중지됨
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="mt-4 rounded-sm bg-surface-container-high p-3 text-caption text-on-surface-variant">
            <p className="font-medium text-on-surface">에이전트는 저장된 설정입니다.</p>
            <p className="mt-1">
              놓은 분류와 도구는 권한 경계입니다. 목록 밖의 도구를 지정한 실행 계획은 통째로
              거부되고, 검색은 목록 밖의 분류에 닿지 않습니다.
            </p>
            <p className="mt-1">
              아무것도 놓지 않으면 전체를 허용한다는 뜻입니다. 제한은 직접 놓아야 걸립니다.
            </p>
          </div>
        </section>

        <form onSubmit={save} className="space-y-4 rounded-md bg-surface-container-low p-4 sm:p-6">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <h2 className="text-title font-medium">
              {editingId ? "에이전트 편집" : "새 에이전트"}
              {dirty && (
                <span className="ml-2 text-caption font-normal text-primary">저장 안 됨</span>
              )}
            </h2>
            <div className="flex flex-wrap gap-2">
              {editingId && (
                <button
                  type="button"
                  onClick={() => {
                    const agent = agents?.find((a) => a.id === editingId);
                    if (agent) setDeleteTarget(agent);
                  }}
                  className="btn-danger btn-compact"
                >
                  삭제
                </button>
              )}
              <button type="submit" disabled={saving} className="btn-filled">
                {saving ? "저장 중..." : editingId ? "저장" : "만들기"}
              </button>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="agent-name" className="text-label font-medium text-on-surface-variant">
                이름
              </label>
              <input
                id="agent-name"
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                required
                maxLength={200}
                placeholder="현장 안전 담당"
                className="field mt-1 w-full"
              />
            </div>
            <div>
              <label
                htmlFor="agent-description"
                className="text-label font-medium text-on-surface-variant"
              >
                설명
              </label>
              <input
                id="agent-description"
                value={draft.description}
                onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                maxLength={2000}
                placeholder="사용자가 채팅에서 고를 때 보이는 한 줄 설명"
                className="field mt-1 w-full"
              />
            </div>
          </div>

          <label className="flex items-center gap-2 text-body">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
              className="h-4 w-4 accent-primary"
            />
            사용 — 끄면 채팅에서 고를 수 없습니다.
          </label>

          <AgentCanvas draft={draft} onChange={setDraft} catalog={catalog} />

          <ErrorBanner message={saveError} />
        </form>
      </div>

      {pendingSelect && (
        <ConfirmDialog
          title="저장하지 않은 변경"
          message="캔버스에 저장하지 않은 변경이 있습니다. 버리고 다른 에이전트를 열까요?"
          confirmLabel="버리고 이동"
          onConfirm={async () => {
            open(pendingSelect.id);
          }}
          onClose={() => setPendingSelect(null)}
        />
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="에이전트 삭제"
          message={`"${deleteTarget.name}" 에이전트를 삭제합니다. 이 에이전트로 만들어진 지난 답변은 그대로 남고 추적 화면에도 계속 이름이 표시됩니다. 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onConfirm={async () => {
            await apiFetch(`/api/agents/${deleteTarget.id}`, { method: "DELETE" });
            await load();
            open(null);
          }}
          onClose={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
