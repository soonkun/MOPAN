"use client";

import { useId } from "react";
import {
  NODE_KIND_LABEL,
  addEdge,
  referenceOptions,
  removeEdge,
  removeNode,
  updateNode,
} from "@/lib/graph";
import {
  ConditionEditor,
  KIND_HELP,
  ValueField,
} from "@/components/workflows/fields";
import { defaultWhen, type Selection } from "@/components/workflows/EditorCanvas";
import type {
  AnswerModel,
  CallableTool,
  Collection,
  GraphNode,
  McpToolOption,
  PromptSummary,
  WorkflowGraph,
  WorkflowVersion,
} from "@/lib/types";

/** The inspector: everything that is a FORM, out of the canvas's way.
 *
 * What it shows follows the selection. A node -> that node's settings, exactly
 * what the old modal dialog held, plus the node's outgoing edges - which is THE
 * KEYBOARD ROUTE to drawing one, kept from the first editor: two selects and a
 * button can still do everything the port drag does. An edge -> its condition
 * and its 제거. Nothing -> the workflow itself: description, permission
 * boundary, prompt, model, versions.
 *
 * The boundary lists keep the first editor's one non-negotiable sentence: an
 * EMPTY list is 전체 허용, not 없음, and the screen says so next to every empty
 * list rather than trusting the reader to know.
 */

export interface Draft {
  name: string;
  description: string;
  prompt_name: string;
  answer_model: string;
  enabled: boolean;
  collection_ids: string[];
  tool_ids: string[];
}

export interface Catalog {
  collections: Collection[];
  tools: McpToolOption[];
  prompts: PromptSummary[];
  models: AnswerModel[];
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-outline-variant px-4 py-4 last:border-b-0">
      <h3 className="text-label font-medium text-on-surface">{title}</h3>
      <div className="mt-2 space-y-3">{children}</div>
    </section>
  );
}

function NodePanel({
  node,
  graph,
  onChangeGraph,
  onSelect,
  callables,
  mcpTools,
  error,
}: {
  node: GraphNode;
  graph: WorkflowGraph;
  onChangeGraph: (next: WorkflowGraph) => void;
  onSelect: (next: Selection) => void;
  callables: CallableTool[];
  mcpTools: McpToolOption[];
  error: { node?: string; edge?: number; text: string } | null;
}) {
  const uid = useId();
  const fixed = node.kind === "input" || node.kind === "answer";
  const options = referenceOptions(graph, node.id);
  const ref = node.tool ?? "rag";
  const ragCollections = callables.find((c) => c.kind === "rag")?.collections ?? [];
  const mcp = ref.startsWith("mcp:")
    ? mcpTools.find((t) => `mcp:${t.server_name}/${t.name}` === ref)
    : undefined;
  const properties = mcp
    ? Object.keys((mcp.input_schema?.properties as Record<string, unknown> | undefined) ?? {})
    : ["query"];
  const args = (node.arguments ?? {}) as Record<string, unknown>;
  const outgoing = graph.edges
    .map((edge, index) => ({ edge, index }))
    .filter(({ edge }) => edge.from === node.id);
  const targets = graph.nodes.filter((n) => n.kind !== "input" && n.id !== node.id);

  const change = (patch: Partial<GraphNode>) => onChangeGraph(updateNode(graph, node.id, patch));

  return (
    <>
      <div className="flex items-start justify-between gap-2 px-4 pt-4">
        <div>
          <h2 className="text-title font-medium">
            {NODE_KIND_LABEL[node.kind]} 노드 · {node.id}
          </h2>
          <p className="mt-1 text-caption text-on-surface-variant">{KIND_HELP[node.kind]}</p>
        </div>
      </div>
      {error?.node === node.id && (
        <p className="mx-4 mt-2 rounded-sm bg-error-container px-2 py-1 text-caption text-on-error-container">
          {error.text}
        </p>
      )}

      <Section title="이름">
        <input
          value={node.label ?? ""}
          onChange={(event) => change({ label: event.target.value })}
          maxLength={120}
          placeholder={NODE_KIND_LABEL[node.kind]}
          className="field w-full"
          aria-label="노드 이름"
        />
      </Section>

      {node.kind === "tool" && (
        <>
          <Section title="부를 것">
            <select
              value={ref}
              onChange={(event) =>
                change({
                  tool: event.target.value,
                  // The arguments belong to the tool that was there. Keeping
                  // them would hand the next tool a field it never declared.
                  arguments: { query: args.query ?? "" },
                  collections: event.target.value === "rag" ? node.collections ?? [] : [],
                })
              }
              className="field w-full"
              aria-label="부를 것"
            >
              {callables.map((callable) => (
                <option key={callable.ref} value={callable.ref}>
                  {callable.name}
                  {callable.kind === "workflow" ? " (워크플로우)" : ""}
                </option>
              ))}
              {!callables.some((c) => c.ref === ref) && (
                <option value={ref}>{ref} (지금은 부를 수 없음)</option>
              )}
            </select>
            <p className="text-caption text-on-surface-variant">
              이 워크플로우의 허용 목록 밖에 있는 도구를 고르면 저장할 때 거부됩니다.
            </p>
          </Section>

          {ref === "rag" && (
            <Section title="검색할 분류">
              <p className="text-caption text-primary">
                하나도 고르지 않으면 이 워크플로우가 허용한 분류 전체를 검색합니다.
              </p>
              {ragCollections.length === 0 && (
                <p className="text-caption text-on-surface-variant">등록된 분류가 없습니다.</p>
              )}
              {ragCollections.map((collection) => (
                <label
                  key={collection.id}
                  className="flex items-center gap-2 text-body text-on-surface"
                >
                  <input
                    type="checkbox"
                    checked={(node.collections ?? []).includes(collection.name)}
                    onChange={(event) =>
                      change({
                        collections: event.target.checked
                          ? [...(node.collections ?? []), collection.name]
                          : (node.collections ?? []).filter((n) => n !== collection.name),
                      })
                    }
                    className="h-4 w-4 shrink-0 accent-primary"
                  />
                  <span className="min-w-0 truncate">{collection.name}</span>
                </label>
              ))}
            </Section>
          )}

          <Section title="인자">
            {properties.map((name) => (
              <ValueField
                key={name}
                id={`${uid}-arg-${name}`}
                label={name}
                value={args[name]}
                options={options}
                onChange={(next) => change({ arguments: { ...args, [name]: next } })}
                help={
                  options.length === 0
                    ? "이 노드로 들어오는 간선이 아직 없어서 참조할 수 있는 값이 없습니다."
                    : undefined
                }
              />
            ))}
          </Section>
        </>
      )}

      {node.kind === "branch" && (
        <Section title="조건">
          <ConditionEditor
            idPrefix={`${uid}-condition`}
            condition={node.condition ?? { kind: "exists", of: "" }}
            options={options}
            onChange={(condition) => change({ condition })}
          />
        </Section>
      )}

      {node.kind !== "answer" && (
        <Section title="나가는 간선">
          <p className="text-caption text-on-surface-variant">
            간선이 실행 순서입니다. 캔버스에서 포트를 끌어도 되고, 여기서 골라 이어도 같은
            간선입니다.
          </p>
          {outgoing.length === 0 ? (
            <p className="text-caption text-on-surface-variant">아직 없습니다.</p>
          ) : (
            <ul className="space-y-1">
              {outgoing.map(({ edge, index }) => (
                <li
                  key={index}
                  className={`rounded-sm px-2 py-1.5 ${
                    error?.edge === index
                      ? "bg-error-container text-on-error-container"
                      : "bg-surface-container"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="min-w-0 truncate text-body">→ {edge.to}</span>
                    <span className="flex shrink-0 items-center gap-1">
                      {node.kind === "branch" && (
                        <select
                          value={edge.when ?? "true"}
                          onChange={(event) =>
                            onChangeGraph({
                              nodes: graph.nodes,
                              edges: graph.edges.map((e, i) =>
                                i === index
                                  ? { ...e, when: event.target.value as "true" | "false" }
                                  : e,
                              ),
                            })
                          }
                          className="field h-8 text-caption"
                          aria-label={`${edge.from} → ${edge.to} 조건`}
                        >
                          <option value="true">참</option>
                          <option value="false">거짓</option>
                        </select>
                      )}
                      <button
                        type="button"
                        onClick={() => onChangeGraph(removeEdge(graph, index))}
                        className="btn-tonal btn-compact"
                      >
                        제거
                      </button>
                    </span>
                  </div>
                  {error?.edge === index && <p className="mt-1 text-caption">{error.text}</p>}
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-end gap-2">
            <div className="min-w-0 flex-1">
              <label
                htmlFor={`${uid}-edge-to`}
                className="block text-label text-on-surface-variant"
              >
                이 노드에서
              </label>
              <select id={`${uid}-edge-to`} className="field mt-1 w-full" defaultValue="">
                <option value="" disabled>
                  고르세요
                </option>
                {targets.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.id} · {n.label?.trim() || NODE_KIND_LABEL[n.kind]}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              onClick={() => {
                const select = document.getElementById(`${uid}-edge-to`) as HTMLSelectElement | null;
                const to = select?.value;
                if (!to) return;
                const when = defaultWhen(graph, node.id);
                onChangeGraph(addEdge(graph, { from: node.id, to, ...(when ? { when } : {}) }));
                if (select) select.value = "";
              }}
              className="btn-tonal btn-compact"
            >
              잇기
            </button>
          </div>
        </Section>
      )}

      <div className="px-4 py-4">
        {fixed ? (
          <p className="text-caption text-on-surface-variant">이 노드는 지울 수 없습니다.</p>
        ) : (
          <button
            type="button"
            onClick={() => {
              onChangeGraph(removeNode(graph, node.id));
              onSelect(null);
            }}
            className="btn-danger btn-compact"
          >
            노드 삭제
          </button>
        )}
      </div>
    </>
  );
}

function EdgePanel({
  index,
  graph,
  onChangeGraph,
  onSelect,
  error,
}: {
  index: number;
  graph: WorkflowGraph;
  onChangeGraph: (next: WorkflowGraph) => void;
  onSelect: (next: Selection) => void;
  error: { node?: string; edge?: number; text: string } | null;
}) {
  const edge = graph.edges[index];
  if (!edge) return null;
  const source = graph.nodes.find((n) => n.id === edge.from);
  return (
    <>
      <div className="px-4 pt-4">
        <h2 className="text-title font-medium">
          간선 · {edge.from} → {edge.to}
        </h2>
        <p className="mt-1 text-caption text-on-surface-variant">
          간선이 실행 순서를 정합니다. 분기에서 나가는 간선에는 참·거짓이 있어야 합니다.
        </p>
      </div>
      {error?.edge === index && (
        <p className="mx-4 mt-2 rounded-sm bg-error-container px-2 py-1 text-caption text-on-error-container">
          {error.text}
        </p>
      )}
      {source?.kind === "branch" && (
        <Section title="조건">
          <select
            value={edge.when ?? "true"}
            onChange={(event) =>
              onChangeGraph({
                nodes: graph.nodes,
                edges: graph.edges.map((e, i) =>
                  i === index ? { ...e, when: event.target.value as "true" | "false" } : e,
                ),
              })
            }
            className="field w-full"
            aria-label="간선 조건"
          >
            <option value="true">참</option>
            <option value="false">거짓</option>
          </select>
        </Section>
      )}
      <div className="px-4 py-4">
        <button
          type="button"
          onClick={() => {
            onChangeGraph(removeEdge(graph, index));
            onSelect(null);
          }}
          className="btn-danger btn-compact"
        >
          간선 제거
        </button>
      </div>
    </>
  );
}

function WorkflowPanel({
  draft,
  onChangeDraft,
  catalog,
  editingId,
  note,
  onNote,
  versions,
  onRollBack,
}: {
  draft: Draft;
  onChangeDraft: (next: Draft) => void;
  catalog: Catalog;
  editingId: string | null;
  note: string;
  onNote: (next: string) => void;
  versions: WorkflowVersion[];
  onRollBack: (version: number) => void;
}) {
  const uid = useId();
  const prompt = catalog.prompts.find((p) => p.name === draft.prompt_name);
  return (
    <>
      <div className="px-4 pt-4">
        <h2 className="text-title font-medium">워크플로우 설정</h2>
        <p className="mt-1 text-caption text-on-surface-variant">
          노드 설정은 캔버스에서 노드를 누르면 따로 열립니다.
        </p>
      </div>

      <Section title="설명">
        <input
          value={draft.description}
          onChange={(event) => onChangeDraft({ ...draft, description: event.target.value })}
          maxLength={2000}
          placeholder="사용자가 채팅에서 @로 고를 때 보이는 한 줄 설명"
          className="field w-full"
          aria-label="설명"
        />
        <label className="flex items-center gap-2 text-body">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => onChangeDraft({ ...draft, enabled: event.target.checked })}
            className="h-4 w-4 accent-primary"
          />
          사용 — 끄면 채팅에서 고를 수 없습니다.
        </label>
      </Section>

      <Section title="허용 분류">
        <p className="text-caption text-on-surface-variant">
          그래프가 닿을 수 있는 권한 경계입니다.{" "}
          <span className="text-primary">하나도 고르지 않으면 제한이 아니라 전체 분류 허용입니다.</span>
        </p>
        {catalog.collections.length === 0 && (
          <p className="text-caption text-on-surface-variant">등록된 분류가 없습니다.</p>
        )}
        {catalog.collections.map((collection) => (
          <label key={collection.id} className="flex items-center gap-2 text-body text-on-surface">
            <input
              type="checkbox"
              checked={draft.collection_ids.includes(collection.id)}
              onChange={(event) =>
                onChangeDraft({
                  ...draft,
                  collection_ids: event.target.checked
                    ? [...draft.collection_ids, collection.id]
                    : draft.collection_ids.filter((id) => id !== collection.id),
                })
              }
              className="h-4 w-4 shrink-0 accent-primary"
            />
            <span className="min-w-0 truncate">{collection.name}</span>
          </label>
        ))}
      </Section>

      <Section title="허용 도구 (MCP)">
        <p className="text-caption text-on-surface-variant">
          목록 밖의 도구를 지정한 그래프는 저장 시점에 통째로 거부됩니다.{" "}
          <span className="text-primary">하나도 고르지 않으면 전체 도구 허용입니다.</span>
        </p>
        {catalog.tools.length === 0 && (
          <p className="text-caption text-on-surface-variant">
            등록된 MCP 도구가 없습니다. MCP 서버가 없는 배포에서는 정상입니다.
          </p>
        )}
        {catalog.tools.map((tool) => (
          <label key={tool.id} className="flex items-center gap-2 text-body text-on-surface">
            <input
              type="checkbox"
              checked={draft.tool_ids.includes(tool.id)}
              onChange={(event) =>
                onChangeDraft({
                  ...draft,
                  tool_ids: event.target.checked
                    ? [...draft.tool_ids, tool.id]
                    : draft.tool_ids.filter((id) => id !== tool.id),
                })
              }
              className="h-4 w-4 shrink-0 accent-primary"
            />
            <span className="min-w-0 truncate">
              {tool.server_name}/{tool.name}
              <span className="text-caption text-on-surface-variant"> · {tool.risk_level}</span>
            </span>
          </label>
        ))}
      </Section>

      <Section title="답변">
        <div>
          <label htmlFor={`${uid}-prompt`} className="block text-label text-on-surface-variant">
            답변 지침 (프롬프트)
          </label>
          <select
            id={`${uid}-prompt`}
            value={draft.prompt_name}
            onChange={(event) => onChangeDraft({ ...draft, prompt_name: event.target.value })}
            className="field mt-1 w-full"
          >
            {catalog.prompts.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
              </option>
            ))}
            {!catalog.prompts.some((p) => p.name === draft.prompt_name) && (
              <option value={draft.prompt_name}>{draft.prompt_name}</option>
            )}
          </select>
          {prompt?.text && (
            <details className="mt-1">
              <summary className="cursor-pointer text-caption text-on-surface-variant">
                본문 미리보기
              </summary>
              <p className="mt-1 whitespace-pre-wrap rounded-sm bg-surface-container p-2 text-caption text-on-surface-variant">
                {prompt.text.slice(0, 800)}
                {prompt.text.length > 800 ? "…" : ""}
              </p>
            </details>
          )}
        </div>
        <div>
          <label htmlFor={`${uid}-model`} className="block text-label text-on-surface-variant">
            답변 모델
          </label>
          <select
            id={`${uid}-model`}
            value={draft.answer_model}
            onChange={(event) => onChangeDraft({ ...draft, answer_model: event.target.value })}
            className="field mt-1 w-full"
          >
            <option value="">배포 기본값</option>
            {catalog.models.map((model) => (
              <option key={model.id} value={model.id}>
                {model.label}
                {model.is_default ? " (기본)" : ""}
              </option>
            ))}
          </select>
        </div>
      </Section>

      {editingId && (
        <Section title="이번 저장 메모">
          <input
            value={note}
            onChange={(event) => onNote(event.target.value)}
            maxLength={500}
            placeholder="무엇을 바꿨는지 한 줄. 되돌릴 때 이것만 보고 고릅니다."
            className="field w-full"
            aria-label="이번 저장 메모"
          />
        </Section>
      )}

      {editingId && versions.length > 0 && (
        <Section title="버전">
          <p className="text-caption text-on-surface-variant">
            저장할 때마다 한 버전이 남습니다. 되돌리기는 그 버전을 다시 활성으로 만들 뿐, 기록을
            지우지 않습니다.
          </p>
          <ul className="space-y-1">
            {versions.map((version) => (
              <li
                key={version.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-sm bg-surface-container px-2 py-1.5"
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
                  </span>
                </span>
                {!version.is_active && (
                  <button
                    type="button"
                    onClick={() => onRollBack(version.version)}
                    className="btn-tonal btn-compact shrink-0"
                  >
                    되돌리기
                  </button>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </>
  );
}

export default function Inspector({
  graph,
  onChangeGraph,
  selection,
  onSelect,
  callables,
  mcpTools,
  error,
  draft,
  onChangeDraft,
  catalog,
  editingId,
  note,
  onNote,
  versions,
  onRollBack,
}: {
  graph: WorkflowGraph;
  onChangeGraph: (next: WorkflowGraph) => void;
  selection: Selection;
  onSelect: (next: Selection) => void;
  callables: CallableTool[];
  mcpTools: McpToolOption[];
  error: { node?: string; edge?: number; text: string } | null;
  draft: Draft;
  onChangeDraft: (next: Draft) => void;
  catalog: Catalog;
  editingId: string | null;
  note: string;
  onNote: (next: string) => void;
  versions: WorkflowVersion[];
  onRollBack: (version: number) => void;
}) {
  const node =
    selection && "node" in selection ? graph.nodes.find((n) => n.id === selection.node) : null;
  const edge = selection && "edge" in selection ? selection.edge : null;

  return (
    <aside
      aria-label="설정"
      className="flex h-full w-80 shrink-0 flex-col overflow-y-auto border-l border-outline-variant bg-surface-container-low"
    >
      {node ? (
        <NodePanel
          node={node}
          graph={graph}
          onChangeGraph={onChangeGraph}
          onSelect={onSelect}
          callables={callables}
          mcpTools={mcpTools}
          error={error}
        />
      ) : edge !== null ? (
        <EdgePanel
          index={edge}
          graph={graph}
          onChangeGraph={onChangeGraph}
          onSelect={onSelect}
          error={error}
        />
      ) : (
        <WorkflowPanel
          draft={draft}
          onChangeDraft={onChangeDraft}
          catalog={catalog}
          editingId={editingId}
          note={note}
          onNote={onNote}
          versions={versions}
          onRollBack={onRollBack}
        />
      )}
    </aside>
  );
}
