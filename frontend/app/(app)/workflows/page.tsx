"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import GraphEditor from "@/components/workflows/GraphEditor";
import WorkflowCanvas, { type Catalog, type Draft } from "@/components/workflows/WorkflowCanvas";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import { placeGraphError, starterGraph } from "@/lib/graph";
import type {
  AnswerModel,
  CallableTool,
  Collection,
  McpToolOption,
  PromptSummary,
  Workflow,
  WorkflowGraph,
  WorkflowVersion,
} from "@/lib/types";

/** 워크플로우 — the screen that was 에이전트 생성.
 *
 * The rename is not cosmetic. An "agent" here was a saved bundle of prompt,
 * corpus scope and tool list that ALSO carried `orchestrator`, and that column
 * is what mixed the two layers: a saved PROCEDURE was switching on autonomous
 * PLANNING. `agents.orchestrator` is gone from the database, so it is gone from
 * this screen; 슈퍼 에이전트 is now only the per-conversation toggle in the
 * composer, where the person choosing it can see it.
 *
 * TWO CANVASES, ONE ROW. The graph is the procedure - what runs, in what order,
 * reading what - and the boundary is what the procedure may reach. They are
 * saved by different requests for a reason the API makes plain: every graph save
 * is a VERSION, and a PATCH that silently made one would hide that. So 저장
 * sends the settings as a PATCH and the graph as a new version, and says which
 * of the two was refused.
 */

const EMPTY: Draft = {
  name: "",
  description: "",
  prompt_name: "answer_agent",
  answer_model: "",
  enabled: true,
  collection_ids: [],
  tool_ids: [],
};

function draftOf(workflow: Workflow): Draft {
  return {
    name: workflow.name,
    description: workflow.description ?? "",
    prompt_name: workflow.prompt_name,
    answer_model: workflow.answer_model ?? "",
    enabled: workflow.enabled,
    collection_ids: workflow.collections.map((c) => c.id),
    tool_ids: workflow.tools.map((t) => t.id),
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
    enabled: draft.enabled,
    collection_ids: draft.collection_ids,
    tool_ids: draft.tool_ids,
  };
}

/** 전체, not 없음. An empty list is unrestricted, and this is the summary an
 * admin reads down the list without opening anything. */
function summary(workflow: Workflow): string {
  const collections =
    workflow.collections.length === 0 ? "전체 분류" : `분류 ${workflow.collections.length}개`;
  const tools = workflow.tools.length === 0 ? "전체 도구" : `도구 ${workflow.tools.length}개`;
  const nodes = workflow.graph?.nodes?.length ?? 0;
  return `${collections} · ${tools} · 노드 ${nodes}개`;
}

export default function WorkflowsPage() {
  // null is "not loaded yet", not an empty list - the distinction every admin
  // screen here draws so the empty state never flashes. Every endpoint behind
  // this page answers a non-admin with 403 관리자 권한이 필요합니다., which lands
  // in loadError, so there is no client-side role branch.
  const [workflows, setWorkflows] = useState<Workflow[] | null>(null);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [tools, setTools] = useState<McpToolOption[]>([]);
  const [prompts, setPrompts] = useState<PromptSummary[]>([]);
  const [models, setModels] = useState<AnswerModel[]>([]);
  // GET /api/tools - the same one list the composer's `@` opens. A node names
  // one of these refs verbatim, which is what makes "부를 수 있는 것" one idea
  // across the two screens instead of two lists that drift.
  const [callables, setCallables] = useState<CallableTool[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  // null = building a new workflow. A string = editing that saved one.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [baseline, setBaseline] = useState<Draft>(EMPTY);
  const [graph, setGraph] = useState<WorkflowGraph>(starterGraph);
  const [graphBaseline, setGraphBaseline] = useState<WorkflowGraph>(starterGraph);
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [note, setNote] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);
  // The graph refusal, already placed on the node or the edge it is about.
  const [graphError, setGraphError] = useState<{
    node?: string;
    edge?: number;
    text: string;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [pendingSelect, setPendingSelect] = useState<{ id: string | null } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Workflow | null>(null);

  const load = useCallback(async () => {
    try {
      setWorkflows(await apiFetch<Workflow[]>("/api/workflows"));
      setLoadError(null);
    } catch (err) {
      setLoadError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
    // Each of these is what a module or a node offers, and each failure is
    // survivable on its own - a deployment with no MCP server has no tools to
    // place, and that is a normal state, not an error over the whole page.
    void apiFetch<Collection[]>("/api/collections").then(setCollections).catch(() => setCollections([]));
    void apiFetch<McpToolOption[]>("/api/mcp/tools").then(setTools).catch(() => setTools([]));
    void apiFetch<PromptSummary[]>("/api/prompts").then(setPrompts).catch(() => setPrompts([]));
    void apiFetch<AnswerModel[]>("/api/models").then(setModels).catch(() => setModels([]));
    void apiFetch<CallableTool[]>("/api/tools").then(setCallables).catch(() => setCallables([]));
  }, [load]);

  const catalog: Catalog = useMemo(
    () => ({ collections, tools, prompts, models }),
    [collections, tools, prompts, models],
  );

  const settingsDirty = JSON.stringify(draft) !== JSON.stringify(baseline);
  const graphDirty = JSON.stringify(graph) !== JSON.stringify(graphBaseline);
  const dirty = settingsDirty || graphDirty;

  const loadVersions = useCallback(async (id: string) => {
    try {
      setVersions(await apiFetch<WorkflowVersion[]>(`/api/workflows/${id}/versions`));
    } catch {
      // The 되돌리기 list is a convenience; failing to load it must not stop the
      // canvas from being edited and saved.
      setVersions([]);
    }
  }, []);

  function open(id: string | null) {
    const workflow = id === null ? null : workflows?.find((w) => w.id === id);
    const next = workflow ? draftOf(workflow) : EMPTY;
    const nextGraph = workflow?.graph ?? starterGraph();
    setEditingId(id);
    setDraft(next);
    setBaseline(next);
    setGraph(nextGraph);
    setGraphBaseline(nextGraph);
    setNote("");
    setSaveError(null);
    setGraphError(null);
    setVersions([]);
    if (id) void loadVersions(id);
  }

  function select(id: string | null) {
    if (dirty) setPendingSelect({ id });
    else open(id);
  }

  /** Editing the graph clears the refusal about it. A message pointing at an
   * edge the user has just deleted would be pointing at whatever now sits at
   * that index, which is worse than no message. */
  function changeGraph(next: WorkflowGraph) {
    setGraph(next);
    setGraphError(null);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setSaveError(null);
    setGraphError(null);
    try {
      if (!editingId) {
        // One request: the graph rides the create, so a new workflow is never
        // saved in the un-runnable state of having no version.
        const saved = await apiFetch<Workflow>("/api/workflows", {
          method: "POST",
          body: JSON.stringify({ ...bodyOf(draft), graph }),
        });
        await load();
        setEditingId(saved.id);
        setBaseline(draftOf(saved));
        setDraft(draftOf(saved));
        setGraphBaseline(saved.graph ?? graph);
        void loadVersions(saved.id);
        return;
      }
      if (settingsDirty) {
        const saved = await apiFetch<Workflow>(`/api/workflows/${editingId}`, {
          method: "PATCH",
          body: JSON.stringify(bodyOf(draft)),
        });
        setBaseline(draftOf(saved));
        setDraft(draftOf(saved));
      }
      if (graphDirty) {
        // EVERY SAVE IS A VERSION, and the new one becomes active. The refusal
        // this can raise is the interesting one, and it is placed rather than
        // banner-ed.
        const version = await apiFetch<WorkflowVersion>(`/api/workflows/${editingId}/versions`, {
          method: "POST",
          body: JSON.stringify({ graph, note: note.trim() || null }),
        });
        setGraphBaseline(version.graph);
        setGraph(version.graph);
        setNote("");
        void loadVersions(editingId);
      }
      await load();
    } catch (err) {
      const message = errorMessage(err);
      // A graph refusal goes to the node or the edge it is about; anything else
      // - a duplicate name, a prompt that does not exist - is about the form.
      const placed = placeGraphError(message, graph);
      if (placed.node !== undefined || placed.edge !== undefined) setGraphError(placed);
      else setSaveError(message);
    } finally {
      setSaving(false);
    }
  }

  async function rollBack(version: number) {
    setSaveError(null);
    try {
      const activated = await apiFetch<WorkflowVersion>(
        `/api/workflows/${editingId}/versions/${version}/activate`,
        { method: "POST" },
      );
      setGraph(activated.graph);
      setGraphBaseline(activated.graph);
      setGraphError(null);
      if (editingId) void loadVersions(editingId);
      await load();
    } catch (err) {
      setSaveError(errorMessage(err));
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6">
      <h1 className="text-headline font-medium">워크플로우</h1>
      <ErrorBanner message={loadError} />

      <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)] lg:items-start">
        <section
          aria-labelledby="saved-workflows"
          className="min-w-0 rounded-md bg-surface-container-low p-4"
        >
          <div className="flex items-center justify-between gap-2">
            <h2 id="saved-workflows" className="text-title font-medium">
              저장된 워크플로우
            </h2>
            <button type="button" onClick={() => select(null)} className="btn-tonal btn-compact">
              새로 만들기
            </button>
          </div>

          {workflows === null ? (
            !loadError && <p className="mt-4 text-body text-on-surface-variant">불러오는 중...</p>
          ) : workflows.length === 0 ? (
            <p className="mt-4 text-body text-on-surface-variant">
              저장된 워크플로우가 없습니다. 하나도 만들지 않으면 채팅은 지금까지와 똑같이
              동작합니다.
            </p>
          ) : (
            <ul className="mt-4 space-y-1">
              {workflows.map((workflow) => {
                const active = editingId === workflow.id;
                return (
                  <li key={workflow.id}>
                    <button
                      type="button"
                      onClick={() => select(workflow.id)}
                      aria-current={active ? "true" : undefined}
                      className={`w-full rounded-md px-3 py-2 text-left transition-colors duration-150 ${
                        active
                          ? "bg-primary-container text-on-primary-container"
                          : "text-on-surface hover:bg-surface-container"
                      }`}
                    >
                      <span className="block truncate text-body font-medium">{workflow.name}</span>
                      <span
                        className={`block truncate text-caption ${
                          active ? "text-on-primary-container" : "text-on-surface-variant"
                        }`}
                      >
                        {summary(workflow)}
                      </span>
                      {!workflow.enabled && (
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
            <p className="font-medium text-on-surface">워크플로우는 저장된 절차입니다.</p>
            <p className="mt-1">
              그래프의 간선이 실행 순서를 정합니다. 놓은 분류와 도구는 권한 경계라서, 목록 밖의
              도구를 지정한 그래프는 저장 시점에 통째로 거부됩니다.
            </p>
            <p className="mt-1">
              아무것도 놓지 않으면 전체를 허용한다는 뜻입니다. 제한은 직접 놓아야 걸립니다.
            </p>
          </div>
        </section>

        <form
          onSubmit={save}
          className="min-w-0 space-y-4 rounded-md bg-surface-container-low p-4 sm:p-6"
        >
          <div className="flex flex-wrap items-end justify-between gap-3">
            <h2 className="text-title font-medium">
              {editingId ? "워크플로우 편집" : "새 워크플로우"}
              {dirty && (
                <span className="ml-2 text-caption font-normal text-primary">저장 안 됨</span>
              )}
            </h2>
            <div className="flex flex-wrap gap-2">
              {editingId && (
                <button
                  type="button"
                  onClick={() => {
                    const workflow = workflows?.find((w) => w.id === editingId);
                    if (workflow) setDeleteTarget(workflow);
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
              <label
                htmlFor="workflow-name"
                className="text-label font-medium text-on-surface-variant"
              >
                이름
              </label>
              <input
                id="workflow-name"
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                required
                maxLength={200}
                placeholder="현장 안전 점검"
                className="field mt-1 w-full"
              />
            </div>
            <div>
              <label
                htmlFor="workflow-description"
                className="text-label font-medium text-on-surface-variant"
              >
                설명
              </label>
              <input
                id="workflow-description"
                value={draft.description}
                onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                maxLength={2000}
                placeholder="사용자가 채팅에서 @로 고를 때 보이는 한 줄 설명"
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

          <GraphEditor
            graph={graph}
            onChange={changeGraph}
            callables={callables}
            mcpTools={tools}
            error={graphError}
          />

          {/* The note rides the VERSION, not the row: it is what this save
              changed, which is the only thing a 되돌리기 list can be read by.
              Hidden while creating, and that is not a layout choice: POST
              /api/workflows takes no note, so a note typed here would be a field
              that saves nowhere - a control that looks like it works and does
              not. It appears with the first 저장, which is the first save that
              has somewhere to put it. */}
          {editingId && (
            <div>
              <label
                htmlFor="version-note"
                className="text-label font-medium text-on-surface-variant"
              >
                이번 저장 메모
              </label>
              <input
                id="version-note"
                value={note}
                onChange={(event) => setNote(event.target.value)}
                maxLength={500}
                placeholder="무엇을 바꿨는지 한 줄. 되돌릴 때 이것만 보고 고릅니다."
                className="field mt-1 w-full"
              />
            </div>
          )}

          {editingId && versions.length > 0 && (
            <section aria-labelledby="version-list" className="rounded-md bg-surface-container-low p-4">
              <h3 id="version-list" className="text-title font-medium">
                버전
              </h3>
              <p className="mt-1 text-caption text-on-surface-variant">
                저장할 때마다 한 버전이 남습니다. 되돌리기는 그 버전을 다시 활성으로 만들 뿐,
                기록을 지우지 않습니다.
              </p>
              <ul className="mt-3 space-y-1">
                {versions.map((version) => (
                  <li
                    key={version.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-sm bg-surface-container px-3 py-2"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-body">
                        v{version.version}
                        {version.is_active && " · 현재"}
                        {version.note ? ` · ${version.note}` : ""}
                      </span>
                      <span className="block truncate text-caption text-on-surface-variant">
                        노드 {version.graph?.nodes?.length ?? 0}개 ·{" "}
                        {new Date(version.created_at).toLocaleString("ko-KR")}
                        {version.created_by_email ? ` · ${version.created_by_email}` : ""}
                      </span>
                    </span>
                    {!version.is_active && (
                      <button
                        type="button"
                        onClick={() => rollBack(version.version)}
                        className="btn-tonal btn-compact shrink-0"
                      >
                        되돌리기
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <WorkflowCanvas draft={draft} onChange={setDraft} catalog={catalog} />

          <ErrorBanner message={saveError} />
          {/* The placed message is rendered on its node or its edge, inside the
              editor. This line only says a refusal happened at all, for someone
              who pressed 저장 and is looking at the button rather than at the
              canvas. */}
          {graphError && (
            <p className="text-body text-error">
              그래프를 저장하지 못했습니다. 문제가 있는 노드나 간선에 이유를 표시했습니다.
            </p>
          )}
        </form>
      </div>

      {pendingSelect && (
        <ConfirmDialog
          title="저장하지 않은 변경"
          message="캔버스에 저장하지 않은 변경이 있습니다. 버리고 다른 워크플로우를 열까요?"
          confirmLabel="버리고 이동"
          onConfirm={async () => {
            open(pendingSelect.id);
          }}
          onClose={() => setPendingSelect(null)}
        />
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="워크플로우 삭제"
          message={`"${deleteTarget.name}" 워크플로우를 삭제합니다. 이 워크플로우로 만들어진 지난 답변은 그대로 남고 추적 화면에도 계속 이름이 표시됩니다. 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onConfirm={async () => {
            await apiFetch(`/api/workflows/${deleteTarget.id}`, { method: "DELETE" });
            await load();
            open(null);
          }}
          onClose={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
