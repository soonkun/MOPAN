"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { apiFetch, errorMessage } from "@/lib/api";
import EditorCanvas, { type Selection } from "@/components/workflows/EditorCanvas";
import Inspector, { type Catalog, type Draft } from "@/components/workflows/Inspector";
import TestRun from "@/components/workflows/TestRun";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { autoLayout, duplicateNode, placeGraphError, starterGraph } from "@/lib/graph";
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
  const nameRef = useRef<HTMLInputElement>(null);
  const [graphError, setGraphError] = useState<{
    node?: string;
    edge?: number;
    text: string;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [selection, setSelection] = useState<Selection>(null);
  // 오른쪽 고정 패널을 없애고 전부 수납했다(소유자 지적: "오른쪽에도 설정으로
  // 수납"). 왼쪽 레일이 워크플로우 설정·도구·노드 순으로 서고, 패널은 레일
  // 옆에 떠서 열린다. "selection"은 노드/간선을 고르면 자동으로 열린다.
  const [panel, setPanel] = useState<null | "settings" | "selection">(null);
  const [paletteOpen, setPaletteOpen] = useState(
    () => typeof window === "undefined" || window.innerWidth >= 640,
  );
  const [testing, setTesting] = useState(false);
  // 실행해보기의 노드별 진행 상태(id -> running/done/skipped/…). 캔버스가 이걸로
  // 테두리를 켠다. 새 실행이 시작되거나 패널을 닫으면 비운다 - 실행이 끝난
  // 직후의 점등은 남겨서 어떤 경로로 답이 나왔는지 읽을 수 있게 한다.
  const [runStates, setRunStates] = useState<Record<string, string>>({});
  const [versionToDelete, setVersionToDelete] = useState<number | null>(null);
  const [leaving, setLeaving] = useState(false);

  const settingsDirty = JSON.stringify(draft) !== JSON.stringify(baseline);
  const graphDirty = JSON.stringify(graph) !== JSON.stringify(graphBaseline);
  const dirty = settingsDirty || graphDirty;
  // 실행해보기는 저장된 그래프를 돌리므로, 막아야 하는 것은 "저장본과 다르게
  // 동작할 변경"뿐이다. 노드를 몇 픽셀 옮긴 것은 동작이 같은데도 실행을 막던
  // 부당함(소유자 지적) - 좌표(x, y)를 뺀 나머지로만 비교한다. 저장 버튼과
  // 나가기 확인은 여전히 dirty(좌표 포함)를 본다: 배치도 저장할 가치가 있다.
  const functionalOf = (g: typeof graph) =>
    JSON.stringify({
      nodes: g.nodes.map(({ x: _x, y: _y, ...rest }) => rest),
      edges: g.edges,
    });
  const functionalDirty = settingsDirty || functionalOf(graph) !== functionalOf(graphBaseline);

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
  // 되돌리기/다시하기. 그래프 변경마다 이전 상태를 쌓는다(좌표 이동 포함 - 배치도 되돌릴 가치가
  // 있다). 상한 100. 저장·불러오기는 이력을 비우지 않는다 - 저장 뒤에도 되돌릴 수 있어야 한다.
  const historyRef = useRef<{ past: WorkflowGraph[]; future: WorkflowGraph[] }>({ past: [], future: [] });
  const [historyTick, setHistoryTick] = useState(0);
  function changeGraph(next: WorkflowGraph) {
    setGraph((prev) => {
      if (prev !== next) {
        historyRef.current.past = [...historyRef.current.past.slice(-99), prev];
        historyRef.current.future = [];
      }
      return next;
    });
    setGraphError(null);
    setHistoryTick((t) => t + 1);
  }
  function undo() {
    const past = historyRef.current.past;
    if (past.length === 0) return;
    setGraph((current) => {
      const previous = past[past.length - 1];
      historyRef.current.past = past.slice(0, -1);
      historyRef.current.future = [current, ...historyRef.current.future].slice(0, 100);
      return previous;
    });
    setGraphError(null);
    setHistoryTick((t) => t + 1);
  }
  function redo() {
    const future = historyRef.current.future;
    if (future.length === 0) return;
    setGraph((current) => {
      const next = future[0];
      historyRef.current.future = future.slice(1);
      historyRef.current.past = [...historyRef.current.past, current];
      return next;
    });
    setGraphError(null);
    setHistoryTick((t) => t + 1);
  }
  void historyTick;
  const canUndo = historyRef.current.past.length > 0;
  const canRedo = historyRef.current.future.length > 0;
  function duplicateSelected() {
    if (!selection || !("node" in selection) || !selection.node) return;
    const next = duplicateNode(graph, selection.node);
    if (next === graph) return;
    changeGraph(next);
    setSelection({ node: next.nodes[next.nodes.length - 1].id });
  }
  // 머리 줄(h-14) 중심에 햄버거를 맞춘다: (56-40)/2 = 8px. 이 화면을 떠나면 되돌린다.
  useEffect(() => {
    document.documentElement.style.setProperty("--hamburger-top", "0.5rem");
    return () => {
      document.documentElement.style.removeProperty("--hamburger-top");
    };
  }, []);
  // 단축키: Ctrl/⌘+Z 되돌리기, Ctrl/⌘+Shift+Z·Ctrl+Y 다시하기, Ctrl/⌘+D 복제, Ctrl/⌘+S 저장.
  // 입력 칸 안에서는 브라우저 기본 동작(글자 되돌리기)이 우선이다.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable);
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      if (key === "s") {
        event.preventDefault();
        void save();
        return;
      }
      if (typing) return;
      if (key === "z" && !event.shiftKey) {
        event.preventDefault();
        undo();
      } else if ((key === "z" && event.shiftKey) || key === "y") {
        event.preventDefault();
        redo();
      } else if (key === "d") {
        event.preventDefault();
        duplicateSelected();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

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
        // 거절이 가리키는 것을 화면과 설정 패널이 같이 보여주게.
        if (placed.node !== undefined) setSelection({ node: placed.node });
        else if (placed.edge !== undefined) setSelection({ edge: placed.edge });
        setPanel("selection");
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

  async function deleteVersion(version: number) {
    setSaveError(null);
    try {
      await apiFetch(`/api/workflows/${editingId}/versions/${version}`, { method: "DELETE" });
      if (editingId) void loadVersions(editingId);
    } catch (err) {
      // 활성 버전을 지우려던 경우의 409 문구가 그대로 온다.
      setSaveError(errorMessage(err));
    } finally {
      setVersionToDelete(null);
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
      <header className="flex h-14 shrink-0 items-center gap-1.5 border-b border-outline-variant bg-surface-container-low py-0 pl-[4.25rem] pr-2 sm:gap-2 sm:pr-3 md:pl-3">
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
          ref={nameRef}
          value={draft.name}
          onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          maxLength={200}
          placeholder="워크플로우 이름"
          aria-label="워크플로우 이름"
          className="h-9 min-w-[6rem] flex-1 rounded-sm bg-transparent px-2 text-title font-medium text-on-surface outline-none placeholder:text-on-surface-variant focus-visible:bg-surface-container sm:max-w-md"
        />
        {dirty && <span className="shrink-0 text-caption text-primary">저장 안 됨</span>}
        {!draft.enabled && (
          <span className="shrink-0 rounded-full bg-surface-container-high px-2 py-0.5 text-caption text-on-surface-variant">
            중지됨
          </span>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-1 sm:gap-2">
          {/* 편집 도구: 되돌리기·다시하기·정렬. 아이콘만, 툴팁이 이름. 모바일에서는
              머리 줄이 좁아 아래 막대로 내려간다(같은 핸들러). */}
          <button type="button" onClick={undo} disabled={!canUndo} aria-label="되돌리기 (Ctrl+Z)" title="되돌리기 (Ctrl+Z)" className="icon-btn hidden h-9 w-9 disabled:opacity-40 sm:flex">
            <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 14 4 9l5-5" />
              <path d="M4 9h10a6 6 0 0 1 0 12h-3" />
            </svg>
          </button>
          <button type="button" onClick={redo} disabled={!canRedo} aria-label="다시하기 (Ctrl+Shift+Z)" title="다시하기 (Ctrl+Shift+Z)" className="icon-btn hidden h-9 w-9 disabled:opacity-40 sm:flex">
            <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="m15 14 5-5-5-5" />
              <path d="M20 9H10a6 6 0 0 0 0 12h3" />
            </svg>
          </button>
          <button type="button" onClick={() => changeGraph(autoLayout(graph))} aria-label="자동 정렬" title="자동 정렬 - 실행 순서대로 열을 맞춥니다" className="icon-btn hidden h-9 w-9 sm:flex">
            <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              <rect x="3" y="4" width="6" height="6" rx="1.5" />
              <rect x="15" y="4" width="6" height="6" rx="1.5" />
              <rect x="15" y="14" width="6" height="6" rx="1.5" />
              <path d="M9 7h6M9 7c3 0 3 10 6 10" />
            </svg>
          </button>
          {/* 아이콘+라벨 가변형(소유자 지정): 모바일은 아이콘만, 데스크톱은
              글자까지. aria-label이 아이콘만 남는 화면의 이름이다. */}
          <button
            type="button"
            onClick={() => setTesting((open) => !open)}
            disabled={!editingId || functionalDirty}
            aria-label="실행해보기"
            title={
              !editingId
                ? "먼저 만들기로 저장해야 실행할 수 있습니다."
                : functionalDirty
                  ? "동작이 바뀌는 저장하지 않은 변경이 있습니다. 저장 후 실행해 주세요."
                  : "저장된 그래프를 채팅과 같은 경로로 실행합니다."
            }
            className="btn-tonal btn-compact gap-1.5 px-2.5 sm:px-4"
            aria-expanded={testing}
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4 shrink-0" fill="currentColor">
              <path d="M8 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 8 5.5Z" />
            </svg>
            <span className="hidden sm:inline">실행해보기</span>
          </button>
          <button
            type="button"
            // 이름이 비어 있으면 버튼을 죽이는 대신 이유를 말한다 - 상단바의 투명한
            // 이름 칸은 모바일에서 눈에 띄지 않아, 조용한 비활성은 "저장이 안 된다"로 읽혔다.
            onClick={() => {
              if (!draft.name.trim()) {
                setSaveError("워크플로우 이름을 먼저 적어 주세요. 상단바의 '워크플로우 이름' 칸입니다.");
                nameRef.current?.focus();
                return;
              }
              void save();
            }}
            disabled={saving}
            aria-label={editingId ? "저장" : "만들기"}
            className="btn-filled btn-compact gap-1.5 px-2.5 sm:px-4"
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 4h11l3 3v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z" />
              <path d="M8 4v5h7V4" />
              <path d="M8 20v-6h8v6" />
            </svg>
            <span className="hidden sm:inline">
              {saving ? "저장 중..." : editingId ? "저장" : "만들기"}
            </span>
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
              // 노드/간선을 고르면 그 설정이 팝업으로, 빈 바닥을 누르면 닫힌다.
              setPanel(next ? "selection" : panel === "selection" ? null : panel);
            }}
            error={graphError}
            callables={callables}
            paletteOpen={paletteOpen}
            onPaletteOpenChange={setPaletteOpen}
            runStates={runStates}
          />

          {/* 왼쪽 레일: 워크플로우 설정 · 도구 · 노드 순(소유자 지정). 패널은
              전부 레일 옆에 떠서 열리고, 캔버스는 항상 전체 화면이다. */}
          <div className="pointer-events-auto absolute inset-x-3 bottom-3 z-20 flex flex-row items-stretch gap-1 rounded-md bg-surface-container p-1 shadow-menu sm:inset-x-auto sm:bottom-auto sm:left-3 sm:top-3 sm:flex-col sm:items-start sm:gap-2 sm:bg-transparent sm:p-0 sm:shadow-none">
            <button
              type="button"
              onClick={() => {
                setPanel((prev) => (prev === "settings" ? null : "settings"));
                setPaletteOpen(false);
              }}
              aria-label="워크플로우 설정"
              aria-expanded={panel === "settings"}
              className={`flex h-10 flex-1 items-center justify-center gap-1.5 rounded-md px-2 text-label sm:flex-none sm:justify-start sm:gap-2 sm:px-3 sm:shadow-menu sm:px-3 ${
                panel === "settings"
                  ? "bg-primary-container text-on-primary-container"
                  : "bg-surface-container text-on-surface hover:bg-surface-container-high"
              }`}
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <path d="M4 7h8M18 7h2M4 17h4M14 17h6" />
                <circle cx="15" cy="7" r="2.2" />
                <circle cx="11" cy="17" r="2.2" />
              </svg>
              <span className="sm:hidden">설정</span><span className="hidden sm:inline">워크플로우 설정</span>
            </button>
            <button
              type="button"
              onClick={() => {
                if (!paletteOpen) setPanel(null);
                setPaletteOpen(!paletteOpen);
              }}
              aria-label="도구"
              aria-expanded={paletteOpen}
              className={`flex h-10 flex-1 items-center justify-center gap-1.5 rounded-md px-2 text-label sm:flex-none sm:justify-start sm:gap-2 sm:px-3 sm:shadow-menu sm:px-3 ${
                paletteOpen
                  ? "bg-primary-container text-on-primary-container"
                  : "bg-surface-container text-on-surface hover:bg-surface-container-high"
              }`}
            >
              {/* 렌치(🔧) - 직접 그린 스패너는 안 읽혔다(소유자 지적). Lucide
                  wrench 경로: 고리 머리 + 대각 손잡이가 이 크기에서도 렌치로
                  읽히는 검증된 모양이다. */}
              <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
              </svg>
              <span>도구</span>
            </button>
            <button
              type="button"
              onClick={() => setPanel((prev) => (prev === "selection" ? null : "selection"))}
              disabled={!selection}
              title={selection ? undefined : "캔버스에서 노드나 간선을 누르면 열립니다."}
              aria-label="노드"
              aria-expanded={panel === "selection"}
              className={`flex h-10 flex-1 items-center justify-center gap-1.5 rounded-md px-2 text-label sm:flex-none sm:justify-start sm:gap-2 sm:px-3 sm:shadow-menu disabled:opacity-50 sm:px-3 ${
                panel === "selection"
                  ? "bg-primary-container text-on-primary-container"
                  : "bg-surface-container text-on-surface hover:bg-surface-container-high"
              }`}
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <rect x="5" y="5" width="14" height="14" rx="2.5" />
                <rect x="9.5" y="9.5" width="5" height="5" rx="1" />
              </svg>
              <span>노드</span>
            </button>
            {/* 모바일 전용: 편집 도구 셋. 데스크톱은 머리 줄에 있다. */}
            <span className="mx-0.5 w-px self-stretch bg-outline-variant sm:hidden" aria-hidden="true" />
            <button type="button" onClick={undo} disabled={!canUndo} aria-label="되돌리기" className="icon-btn h-10 w-9 disabled:opacity-40 sm:hidden">
              <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 14 4 9l5-5" />
                <path d="M4 9h10a6 6 0 0 1 0 12h-3" />
              </svg>
            </button>
            <button type="button" onClick={redo} disabled={!canRedo} aria-label="다시하기" className="icon-btn h-10 w-9 disabled:opacity-40 sm:hidden">
              <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="m15 14 5-5-5-5" />
                <path d="M20 9H10a6 6 0 0 0 0 12h3" />
              </svg>
            </button>
            <button type="button" onClick={() => changeGraph(autoLayout(graph))} aria-label="자동 정렬" className="icon-btn h-10 w-9 sm:hidden">
              <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <rect x="3" y="4" width="6" height="6" rx="1.5" />
                <rect x="15" y="4" width="6" height="6" rx="1.5" />
                <rect x="15" y="14" width="6" height="6" rx="1.5" />
                <path d="M9 7h6M9 7c3 0 3 10 6 10" />
              </svg>
            </button>
          </div>

          {/* 수납 패널 - 도구 서랍과 같은 자리(레일 옆)에서 열린다. 모바일은 아래에서
              올라오는 시트: 손잡이 줄과 닫기 버튼이 있고, 높이는 화면의 절반 남짓. */}
          {(panel === "settings" || (panel === "selection" && selection)) && (
            <div className="pointer-events-auto absolute inset-x-0 bottom-[3.75rem] top-auto z-10 flex max-h-[56%] flex-col overflow-hidden rounded-t-xl bg-surface-container-low shadow-dialog sm:inset-x-auto sm:bottom-3 sm:left-3 sm:top-[9.75rem] sm:max-h-none sm:w-80 sm:max-w-[calc(100%-1.5rem)] sm:flex-row sm:rounded-md sm:shadow-menu">
              <div className="flex shrink-0 items-center justify-between px-3 pt-2 sm:hidden">
                <span className="mx-auto h-1 w-10 rounded-full bg-outline-variant" aria-hidden="true" />
                <button
                  type="button"
                  onClick={() => setPanel(null)}
                  aria-label="패널 닫기"
                  className="icon-btn absolute right-2 top-1.5 h-8 w-8"
                >
                  <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                    <path d="M6 6l12 12M18 6 6 18" />
                  </svg>
                </button>
              </div>
              <Inspector
                graph={graph}
                onChangeGraph={changeGraph}
                selection={panel === "settings" ? null : selection}
                onSelect={(next) => {
                  setSelection(next);
                  if (!next) setPanel(null);
                }}
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
                onDeleteVersion={setVersionToDelete}
                onDuplicate={duplicateSelected}
              />
            </div>
          )}

          {testing && editingId && (
            <TestRun
              workflowId={editingId}
              onClose={() => {
                setTesting(false);
                setRunStates({});
              }}
              onRunStart={() => setRunStates({})}
              onStep={(id, state) => setRunStates((prev) => ({ ...prev, [id]: state }))}
            />
          )}
        </div>
      </div>

      {versionToDelete !== null && (
        <ConfirmDialog
          title="버전 삭제"
          message={`v${versionToDelete}을(를) 기록에서 지웁니다. 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onConfirm={() => deleteVersion(versionToDelete)}
          onClose={() => setVersionToDelete(null)}
        />
      )}

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
    </div>
  );
}
