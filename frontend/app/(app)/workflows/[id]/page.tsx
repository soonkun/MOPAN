"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { apiFetch, errorMessage } from "@/lib/api";
import EditorCanvas, { type Selection } from "@/components/workflows/EditorCanvas";
import Inspector, { type Catalog, type Draft } from "@/components/workflows/Inspector";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
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

/** The workflow EDITOR - the whole viewport, like a drawing app.
 *
 * The first editor put a 220px canvas strip inside a scrolling form between the
 * name field and the version list, and a second canvas below that for the
 * permission boundary. The complaint that killed it was exact: the canvas was
 * tiny and the tools filled the room. This screen inverts it - the canvas IS
 * the screen, the tools live in a drawer that collapses to a button, and every
 * form control is in the inspector on the right.
 *
 * WHAT DID NOT CHANGE is the save contract, kept verbatim from the first
 * editor: a new workflow POSTs settings and graph in ONE request so it is never
 * saved un-runnable; on an existing one 저장 sends the settings as a PATCH and
 * the graph as a NEW VERSION (every graph save is a version, silently PATCHing
 * one would hide that); and the server's Korean refusal is placed on the node
 * or edge it is about by `placeGraphError`, never rewritten.
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

export default function WorkflowEditorPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const isNew = params.id === "new";
  const [editingId, setEditingId] = useState<string | null>(isNew ? null : params.id);

  const [collections, setCollections] = useState<Collection[]>([]);
  const [tools, setTools] = useState<McpToolOption[]>([]);
  const [prompts, setPrompts] = useState<PromptSummary[]>([]);
  const [models, setModels] = useState<AnswerModel[]>([]);
  const [callables, setCallables] = useState<CallableTool[]>([]);

  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [baseline, setBaseline] = useState<Draft>(EMPTY);
  const [graph, setGraph] = useState<WorkflowGraph>(starterGraph);
  const [graphBaseline, setGraphBaseline] = useState<WorkflowGraph>(starterGraph);
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [note, setNote] = useState("");
  const [loaded, setLoaded] = useState(isNew);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [graphError, setGraphError] = useState<{
    node?: string;
    edge?: number;
    text: string;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [selection, setSelection] = useState<Selection>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const settingsDirty = JSON.stringify(draft) !== JSON.stringify(baseline);
  const graphDirty = JSON.stringify(graph) !== JSON.stringify(graphBaseline);
  const dirty = settingsDirty || graphDirty;

  const loadVersions = useCallback(async (id: string) => {
    try {
      setVersions(await apiFetch<WorkflowVersion[]>(`/api/workflows/${id}/versions`));
    } catch {
      // The 되돌리기 list is a convenience; failing to load it must not stop
      // the canvas from being edited and saved.
      setVersions([]);
    }
  }, []);

  useEffect(() => {
    // Each catalogue failure is survivable on its own - a deployment with no
    // MCP server has no tools to offer, and that is a normal state.
    void apiFetch<Collection[]>("/api/collections").then(setCollections).catch(() => setCollections([]));
    void apiFetch<McpToolOption[]>("/api/mcp/tools").then(setTools).catch(() => setTools([]));
    void apiFetch<PromptSummary[]>("/api/prompts").then(setPrompts).catch(() => setPrompts([]));
    void apiFetch<AnswerModel[]>("/api/models").then(setModels).catch(() => setModels([]));
    void apiFetch<CallableTool[]>("/api/tools").then(setCallables).catch(() => setCallables([]));
    if (!isNew) {
      apiFetch<Workflow>(`/api/workflows/${params.id}`)
        .then((workflow) => {
          const next = draftOf(workflow);
          setDraft(next);
          setBaseline(next);
          const nextGraph = workflow.graph ?? starterGraph();
          setGraph(nextGraph);
          setGraphBaseline(nextGraph);
          setLoaded(true);
          void loadVersions(workflow.id);
        })
        .catch((err) => setLoadError(errorMessage(err)));
    }
  }, [isNew, params.id, loadVersions]);

  // 저장 안 한 변경이 있는 채로 탭을 닫으면 브라우저가 묻는다. 앱 안 이동은
  // 아래 leaving 다이얼로그가 맡는다.
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const catalog: Catalog = useMemo(
    () => ({ collections, tools, prompts, models }),
    [collections, tools, prompts, models],
  );

  /** Editing the graph clears the refusal about it. A message pointing at an
   * edge the user has just deleted would be pointing at whatever now sits at
   * that index, which is worse than no message. */
  function changeGraph(next: WorkflowGraph) {
    setGraph(next);
    setGraphError(null);
  }

  async function save() {
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
        setEditingId(saved.id);
        setBaseline(draftOf(saved));
        setDraft(draftOf(saved));
        setGraphBaseline(saved.graph ?? graph);
        void loadVersions(saved.id);
        window.history.replaceState(null, "", `/workflows/${saved.id}`);
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
        // EVERY SAVE IS A VERSION, and the new one becomes active.
        const version = await apiFetch<WorkflowVersion>(`/api/workflows/${editingId}/versions`, {
          method: "POST",
          body: JSON.stringify({ graph, note: note.trim() || null }),
        });
        setGraphBaseline(version.graph);
        setGraph(version.graph);
        setNote("");
        void loadVersions(editingId);
      }
    } catch (err) {
      const message = errorMessage(err);
      // A graph refusal goes to the node or the edge it is about; anything else
      // - a duplicate name, a prompt that does not exist - is about the form.
      const placed = placeGraphError(message, graph);
      if (placed.node !== undefined || placed.edge !== undefined) {
        setGraphError(placed);
        // 거절이 가리키는 것을 화면과 인스펙터가 같이 보여주게.
        if (placed.node !== undefined) setSelection({ node: placed.node });
        else if (placed.edge !== undefined) setSelection({ edge: placed.edge });
      } else setSaveError(message);
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
    } catch (err) {
      setSaveError(errorMessage(err));
    }
  }

  if (loadError) {
    return (
      <div className="p-6">
        <p className="rounded-md bg-error-container p-4 text-body text-on-error-container">
          {loadError}
        </p>
      </div>
    );
  }
  if (!loaded) {
    return <p className="p-6 text-body text-on-surface-variant">불러오는 중...</p>;
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 상단 바 - 이 화면의 유일한 가로 크롬. 나머지는 전부 캔버스다. */}
      <header className="flex h-14 shrink-0 items-center gap-2 border-b border-outline-variant bg-surface-container-low px-3 pr-16">
        <button
          type="button"
          onClick={() => (dirty ? setLeaving(true) : router.push("/workflows"))}
          className="icon-btn h-9 w-9 shrink-0"
          aria-label="워크플로우 목록으로"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="m15 6-6 6 6 6" />
          </svg>
        </button>
        <input
          value={draft.name}
          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          maxLength={200}
          placeholder="워크플로우 이름"
          aria-label="워크플로우 이름"
          className="h-9 min-w-0 flex-1 rounded-sm bg-transparent px-2 text-title font-medium text-on-surface outline-none placeholder:text-on-surface-variant focus-visible:bg-surface-container sm:max-w-md"
        />
        {dirty && <span className="shrink-0 text-caption text-primary">저장 안 됨</span>}
        {!draft.enabled && (
          <span className="shrink-0 rounded-full bg-surface-container-high px-2 py-0.5 text-caption text-on-surface-variant">
            중지됨
          </span>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {editingId && (
            <button
              type="button"
              onClick={() => setDeleting(true)}
              className="btn-danger btn-compact hidden sm:inline-flex"
            >
              삭제
            </button>
          )}
          <button
            type="button"
            onClick={() => setInspectorOpen((open) => !open)}
            className="btn-tonal btn-compact lg:hidden"
            aria-expanded={inspectorOpen}
          >
            설정
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving || !draft.name.trim()}
            className="btn-filled btn-compact"
          >
            {saving ? "저장 중..." : editingId ? "저장" : "만들기"}
          </button>
        </div>
      </header>

      {saveError && (
        <p className="border-b border-outline-variant bg-error-container px-4 py-2 text-body text-on-error-container">
          {saveError}
        </p>
      )}
      {graphError && (
        <p className="border-b border-outline-variant bg-surface-container-low px-4 py-2 text-caption text-error">
          그래프를 저장하지 못했습니다. 문제가 있는 노드나 간선에 이유를 표시했습니다.
        </p>
      )}

      <div className="relative flex min-h-0 flex-1">
        <div className="relative min-w-0 flex-1">
          <EditorCanvas
            graph={graph}
            onChange={changeGraph}
            selection={selection}
            onSelect={(next) => {
              setSelection(next);
              if (next) setInspectorOpen(true);
            }}
            error={graphError}
            callables={callables}
          />
        </div>
        <div
          className={`${
            inspectorOpen ? "flex" : "hidden"
          } absolute inset-y-0 right-0 z-20 shadow-menu lg:static lg:flex lg:shadow-none`}
        >
          <Inspector
            graph={graph}
            onChangeGraph={changeGraph}
            selection={selection}
            onSelect={setSelection}
            callables={callables}
            mcpTools={tools}
            error={graphError}
            draft={draft}
            onChangeDraft={setDraft}
            catalog={catalog}
            editingId={editingId}
            note={note}
            onNote={setNote}
            versions={versions}
            onRollBack={(v) => void rollBack(v)}
          />
        </div>
      </div>

      {leaving && (
        <ConfirmDialog
          title="저장하지 않은 변경"
          message="캔버스에 저장하지 않은 변경이 있습니다. 버리고 목록으로 돌아갈까요?"
          confirmLabel="버리고 이동"
          onConfirm={async () => {
            router.push("/workflows");
          }}
          onClose={() => setLeaving(false)}
        />
      )}

      {deleting && editingId && (
        <ConfirmDialog
          title="워크플로우 삭제"
          message={`"${draft.name}" 워크플로우를 삭제합니다. 이 워크플로우로 만들어진 지난 답변은 그대로 남고 추적 화면에도 계속 이름이 표시됩니다. 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onConfirm={async () => {
            await apiFetch(`/api/workflows/${editingId}`, { method: "DELETE" });
            router.push("/workflows");
          }}
          onClose={() => setDeleting(false)}
        />
      )}
    </div>
  );
}
