"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  NODE_KIND_LABEL,
  addEdge,
  addNode,
  conditionText,
  referenceOptions,
  removeEdge,
  removeNode,
  updateNode,
} from "@/lib/graph";
import type {
  CallableTool,
  GraphCondition,
  GraphNode,
  McpToolOption,
  WorkflowGraph,
} from "@/lib/types";

/** The workflow graph, drawn.
 *
 * **THE EDGES DRIVE EXECUTION.** That is what changed, and it is why this screen
 * now has ports and positions where the old canvas deliberately had neither: an
 * edge here decides what runs after what, and `{{n1.count}}` reads the result
 * across it. Reordering two boxes changes the answer, so the picture is finally
 * allowed to be a claim about behaviour.
 *
 * NO GRAPH LIBRARY. react-flow is ~50KB gzipped against five runtime
 * dependencies, and what it would buy is pan/zoom, minimaps and drag-to-connect
 * - none of which is the job here. The job is: place a box, join two boxes, and
 * be able to do both from a keyboard on a 390px phone. That is absolutely
 * positioned cards over one <svg> of <path> elements, which is what this is.
 *
 * DRAG IS NEVER THE ROUTE. Every node is a <button>: Enter opens its settings,
 * the arrow keys move it, Delete removes it. Every edge is a row in a list with
 * its own 제거 button, and a new edge is made from two <select>s and a button -
 * not by dragging between ports, which cannot be done without a pointer at all.
 * Pointer dragging is layered on top and costs three handlers.
 *
 * THE SERVER'S REFUSAL GOES WHERE THE MISTAKE IS. `placeGraphError` decides
 * which node or which edge a Korean 400 belongs to; this file renders it there,
 * verbatim, and only falls back to a banner for the refusals that really are
 * about the whole graph.
 */

const CARD_WIDTH = 200;
const CARD_HEIGHT = 84;
const GRID = 20;
const PAD = 24;

const KIND_HELP: Record<GraphNode["kind"], string> = {
  input: "사용자의 질문이 여기서 들어옵니다. 그래프당 하나이며 지울 수 없습니다.",
  tool: "도구를 한 번 부릅니다. 문서 검색·MCP 도구·다른 워크플로우가 모두 여기입니다.",
  branch: "조건을 보고 나가는 간선 중 참 또는 거짓 하나를 고릅니다.",
  answer: "모인 근거로 답합니다. 그래프당 하나이며 지울 수 없습니다.",
};

/** What a tool node shows on its card without being opened. */
function nodeDetail(node: GraphNode): string {
  if (node.kind === "tool") {
    const ref = node.tool ?? "rag";
    const query = String((node.arguments as { query?: unknown } | undefined)?.query ?? "");
    const scope = node.collections?.length ? node.collections.join(", ") : "전체 분류";
    if (ref === "rag") return `문서 검색 · ${scope}${query ? ` · ${query}` : ""}`;
    return query ? `${ref} · ${query}` : ref;
  }
  if (node.kind === "branch") return conditionText(node.condition);
  return KIND_HELP[node.kind];
}

/** A value that is a whole `{{…}}` reference, a number, a boolean or a string.
 *
 * The parse is here rather than at save because the SHAPE matters to the server:
 * `{"right": 0}` and `{"right": "0"}` compare differently, and a condition that
 * silently compares a count against the STRING "0" is refused with 분기 조건의
 * 모양이 올바르지 않습니다. at run time on somebody's question. */
function parseLiteral(raw: string): unknown {
  const value = raw.trim();
  if (value === "") return "";
  if (value === "true") return true;
  if (value === "false") return false;
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  return value;
}

function literalText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

/** One argument value: a reference picked from the list, or something typed.
 *
 * The two are exclusive on purpose. A reference must be the ENTIRE value - a
 * template like `"제목: {{n1.top.title}}"` is refused at save - so a control that
 * let both be true at once would be a control whose whole output the server
 * rejects. */
function ValueField({
  id,
  label,
  value,
  options,
  onChange,
  help,
}: {
  id: string;
  label: string;
  value: unknown;
  options: { value: string; label: string }[];
  onChange: (next: unknown) => void;
  help?: string;
}) {
  const text = literalText(value);
  const isReference = text.startsWith("{{");
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-label font-medium text-on-surface-variant">
        {label}
      </label>
      <select
        id={id}
        value={isReference ? text : ""}
        onChange={(event) => onChange(event.target.value === "" ? "" : event.target.value)}
        className="field w-full"
      >
        <option value="">직접 입력</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
        {/* A reference the graph carries but this node can no longer make -
            an edge was deleted under it - stays visible rather than silently
            becoming 직접 입력 and being saved as literal text. */}
        {isReference && !options.some((o) => o.value === text) && (
          <option value={text}>{text} (지금은 이어져 있지 않음)</option>
        )}
      </select>
      {!isReference && (
        <input
          value={text}
          onChange={(event) => onChange(parseLiteral(event.target.value))}
          className="field w-full"
          placeholder="값을 직접 입력"
        />
      )}
      {help && <p className="text-caption text-on-surface-variant">{help}</p>}
    </div>
  );
}

/** The condition on a branch node. Recursive, because `and`/`or`/`not` take
 * conditions - and capped, because a person who needs four levels of nesting
 * needs a second branch node, not a deeper form. */
function ConditionEditor({
  idPrefix,
  condition,
  options,
  onChange,
  depth = 0,
}: {
  idPrefix: string;
  condition: GraphCondition;
  options: { value: string; label: string }[];
  onChange: (next: GraphCondition) => void;
  depth?: number;
}) {
  const parts = Array.isArray(condition.of) ? (condition.of as GraphCondition[]) : [];
  return (
    <div className={depth > 0 ? "rounded-sm bg-surface-container p-3" : ""}>
      <label htmlFor={`${idPrefix}-kind`} className="block text-label font-medium text-on-surface-variant">
        판단 방식
      </label>
      <select
        id={`${idPrefix}-kind`}
        value={condition.kind}
        onChange={(event) => {
          const kind = event.target.value as GraphCondition["kind"];
          if (kind === "compare") onChange({ kind, left: "", op: ">", right: 0 });
          else if (kind === "and" || kind === "or") onChange({ kind, of: parts.length ? parts : [{ kind: "exists", of: "" }] });
          else if (kind === "not") onChange({ kind, of: { kind: "exists", of: "" } });
          else onChange({ kind, of: "" });
        }}
        className="field mt-1 w-full"
      >
        <option value="compare">값 비교</option>
        <option value="exists">값이 있음</option>
        <option value="empty">값이 비어 있음</option>
        {depth < 2 && <option value="and">모두 참 (and)</option>}
        {depth < 2 && <option value="or">하나라도 참 (or)</option>}
        {depth < 2 && <option value="not">아님 (not)</option>}
      </select>

      {condition.kind === "compare" && (
        <div className="mt-3 space-y-3">
          <ValueField
            id={`${idPrefix}-left`}
            label="왼쪽"
            value={condition.left}
            options={options}
            onChange={(left) => onChange({ ...condition, left })}
          />
          <div>
            <label
              htmlFor={`${idPrefix}-op`}
              className="block text-label font-medium text-on-surface-variant"
            >
              비교
            </label>
            <select
              id={`${idPrefix}-op`}
              value={condition.op ?? ">"}
              onChange={(event) =>
                onChange({ ...condition, op: event.target.value as GraphCondition["op"] })
              }
              className="field mt-1 w-full"
            >
              {["==", "!=", ">", ">=", "<", "<="].map((op) => (
                <option key={op} value={op}>
                  {op}
                </option>
              ))}
            </select>
          </div>
          <ValueField
            id={`${idPrefix}-right`}
            label="오른쪽"
            value={condition.right}
            options={options}
            onChange={(right) => onChange({ ...condition, right })}
            help="숫자는 숫자로, true/false는 참·거짓으로 저장됩니다."
          />
        </div>
      )}

      {(condition.kind === "exists" || condition.kind === "empty") && (
        <div className="mt-3">
          <ValueField
            id={`${idPrefix}-of`}
            label="대상"
            value={condition.of}
            options={options}
            onChange={(of) => onChange({ ...condition, of })}
          />
        </div>
      )}

      {condition.kind === "not" && (
        <div className="mt-3">
          <ConditionEditor
            idPrefix={`${idPrefix}-n`}
            condition={(condition.of as GraphCondition) ?? { kind: "exists", of: "" }}
            options={options}
            depth={depth + 1}
            onChange={(of) => onChange({ kind: "not", of })}
          />
        </div>
      )}

      {(condition.kind === "and" || condition.kind === "or") && (
        <div className="mt-3 space-y-2">
          {parts.map((part, index) => (
            <div key={index} className="space-y-1">
              <ConditionEditor
                idPrefix={`${idPrefix}-${index}`}
                condition={part}
                options={options}
                depth={depth + 1}
                onChange={(next) =>
                  onChange({
                    ...condition,
                    of: parts.map((p, i) => (i === index ? next : p)),
                  })
                }
              />
              {parts.length > 1 && (
                <button
                  type="button"
                  onClick={() => onChange({ ...condition, of: parts.filter((_, i) => i !== index) })}
                  className="btn-tonal btn-compact"
                >
                  이 조건 빼기
                </button>
              )}
            </div>
          ))}
          <button
            type="button"
            onClick={() => onChange({ ...condition, of: [...parts, { kind: "exists", of: "" }] })}
            className="btn-tonal btn-compact"
          >
            조건 추가
          </button>
        </div>
      )}

      {depth === 0 && (
        <p className="mt-3 text-caption text-on-surface-variant">
          모델이 판단하는 분기(kind: llm)는 스키마에 자리는 있지만 아직 켜져 있지 않습니다. 그래서
          여기에 없고, 직접 넣어 저장하면 거부됩니다 — 분기마다 모델을 부르는 비용은 소유자가 보고
          켜는 것이 맞습니다.
        </p>
      )}
    </div>
  );
}

function NodeDialog({
  node,
  graph,
  callables,
  mcpTools,
  onChange,
  onRemove,
  onClose,
}: {
  node: GraphNode;
  graph: WorkflowGraph;
  callables: CallableTool[];
  mcpTools: McpToolOption[];
  onChange: (patch: Partial<GraphNode>) => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialogRef.current?.showModal();
  }, []);

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

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="node-dialog-title"
      onClose={onClose}
      className="w-full max-w-md rounded-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim"
    >
      <div className="max-h-[80vh] overflow-y-auto p-6">
        <h2 id="node-dialog-title" className="text-title font-medium">
          {NODE_KIND_LABEL[node.kind]} 노드 · {node.id}
        </h2>
        <p className="mt-1 text-caption text-on-surface-variant">{KIND_HELP[node.kind]}</p>

        <div className="mt-4 space-y-4">
          <div>
            <label htmlFor="node-label" className="block text-label font-medium text-on-surface-variant">
              이름
            </label>
            <input
              id="node-label"
              value={node.label ?? ""}
              onChange={(event) => onChange({ label: event.target.value })}
              maxLength={120}
              placeholder={NODE_KIND_LABEL[node.kind]}
              className="field mt-1 w-full"
            />
          </div>

          {node.kind === "tool" && (
            <>
              <div>
                <label htmlFor="node-tool" className="block text-label font-medium text-on-surface-variant">
                  부를 것
                </label>
                <select
                  id="node-tool"
                  value={ref}
                  onChange={(event) =>
                    onChange({
                      tool: event.target.value,
                      // The arguments belong to the tool that was there. Keeping
                      // them would hand the next tool a field it never declared.
                      arguments: { query: args.query ?? "" },
                      collections: event.target.value === "rag" ? node.collections ?? [] : [],
                    })
                  }
                  className="field mt-1 w-full"
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
                <p className="mt-1 text-caption text-on-surface-variant">
                  이 워크플로우의 허용 목록 밖에 있는 도구를 고르면 저장할 때 거부됩니다.
                </p>
              </div>

              {ref === "rag" && (
                <fieldset className="border-0 p-0">
                  <legend className="text-label font-medium text-on-surface-variant">
                    검색할 분류
                  </legend>
                  <p className="mt-1 text-caption text-primary">
                    하나도 고르지 않으면 이 워크플로우가 허용한 분류 전체를 검색합니다.
                  </p>
                  <div className="mt-2 space-y-1">
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
                            onChange({
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
                  </div>
                </fieldset>
              )}

              {properties.map((name) => (
                <ValueField
                  key={name}
                  id={`node-arg-${name}`}
                  label={`인자 · ${name}`}
                  value={args[name]}
                  options={options}
                  onChange={(next) => onChange({ arguments: { ...args, [name]: next } })}
                  help={
                    options.length === 0
                      ? "이 노드로 들어오는 간선이 아직 없어서 참조할 수 있는 값이 없습니다."
                      : undefined
                  }
                />
              ))}
            </>
          )}

          {node.kind === "branch" && (
            <ConditionEditor
              idPrefix="node-condition"
              condition={node.condition ?? { kind: "exists", of: "" }}
              options={options}
              onChange={(condition) => onChange({ condition })}
            />
          )}
        </div>

        <div className="mt-6 flex justify-between gap-2">
          {fixed ? (
            <span className="text-caption text-on-surface-variant">이 노드는 지울 수 없습니다.</span>
          ) : (
            <button type="button" onClick={onRemove} className="btn-danger btn-compact">
              노드 삭제
            </button>
          )}
          <button
            type="button"
            onClick={() => dialogRef.current?.close()}
            className="btn-filled btn-compact"
          >
            닫기
          </button>
        </div>
      </div>
    </dialog>
  );
}

export default function GraphEditor({
  graph,
  onChange,
  callables,
  mcpTools,
  error,
}: {
  graph: WorkflowGraph;
  onChange: (next: WorkflowGraph) => void;
  callables: CallableTool[];
  mcpTools: McpToolOption[];
  /** The server's refusal, already placed by `placeGraphError`. Null once the
   * graph has been changed - a message about the shape that was refused stops
   * being true the moment somebody edits that shape. */
  error: { node?: string; edge?: number; text: string } | null;
}) {
  const uid = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [edgeFrom, setEdgeFrom] = useState("");
  const [edgeTo, setEdgeTo] = useState("");
  const [edgeWhen, setEdgeWhen] = useState<"true" | "false">("true");
  // The node a pointer is dragging, and where inside the card it was grabbed.
  // `moved` is what tells a drag from a click: a pointer press on a <button>
  // still fires a click when it is released, so without it every drag ended by
  // opening the settings dialog over the node just moved. Measured by dragging
  // one; the dialog was open at the end of it.
  const dragRef = useRef<{ id: string; dx: number; dy: number; moved: boolean } | null>(null);
  const draggedRef = useRef(false);
  const [focusId, setFocusId] = useState<string | null>(null);

  useEffect(() => {
    if (!focusId) return;
    rootRef.current?.querySelector<HTMLElement>(`[data-node="${CSS.escape(focusId)}"]`)?.focus();
    setFocusId(null);
  }, [focusId, graph]);

  const minX = Math.min(0, ...graph.nodes.map((n) => n.x));
  const minY = Math.min(0, ...graph.nodes.map((n) => n.y));
  const width = Math.max(...graph.nodes.map((n) => n.x - minX), 0) + CARD_WIDTH + PAD * 2;
  const height = Math.max(...graph.nodes.map((n) => n.y - minY), 0) + CARD_HEIGHT + PAD * 2;
  const at = (node: GraphNode) => ({ x: node.x - minX + PAD, y: node.y - minY + PAD });
  const byId = (id: string) => graph.nodes.find((n) => n.id === id);
  const openNode = openId ? byId(openId) : null;
  const fromNode = byId(edgeFrom);

  function move(id: string, dx: number, dy: number) {
    const node = byId(id);
    if (!node) return;
    onChange(updateNode(graph, id, { x: node.x + dx, y: node.y + dy }));
  }

  function add(kind: GraphNode["kind"]) {
    const next = addNode(graph, kind);
    const node = next.nodes[next.nodes.length - 1];
    onChange(next);
    setSelected(node.id);
    setFocusId(node.id);
    setStatus(`${NODE_KIND_LABEL[kind]} 노드 ${node.id}을(를) 추가했습니다. 설정을 열어 채워 주세요.`);
  }

  function drop(id: string) {
    onChange(removeNode(graph, id));
    setSelected(null);
    setStatus(`${id} 노드와 그 노드에 붙어 있던 간선을 지웠습니다.`);
  }

  function join() {
    if (!edgeFrom || !edgeTo || edgeFrom === edgeTo) return;
    const when = fromNode?.kind === "branch" ? edgeWhen : undefined;
    onChange(addEdge(graph, { from: edgeFrom, to: edgeTo, ...(when ? { when } : {}) }));
    setStatus(`${edgeFrom} → ${edgeTo} 간선을 그었습니다.${when ? ` (${when === "true" ? "참" : "거짓"})` : ""}`);
  }

  const nodeError = (id: string) => (error?.node === id ? error.text : null);

  return (
    <div ref={rootRef} className="min-w-0 space-y-3">
      <div className="min-w-0 rounded-md bg-surface-container-low p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-title font-medium">그래프</h3>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => add("tool")} className="btn-tonal btn-compact">
              도구 노드 추가
            </button>
            <button type="button" onClick={() => add("branch")} className="btn-tonal btn-compact">
              분기 노드 추가
            </button>
          </div>
        </div>
        <p className="mt-1 text-caption text-on-surface-variant">
          간선이 실행 순서를 정합니다. 노드를 누르면 설정이 열리고, 방향키로 자리를 옮기고, Delete로
          지웁니다. 끌어서 옮길 수도 있지만 그 방법 없이도 전부 됩니다.
        </p>

        {/* The canvas. overflow-x on the OUTER box and a fixed-width inner box:
            at 390px a four-node graph is 1100px wide, and the honest answer is a
            scroll bar rather than boxes stacked into a picture that is no longer
            the graph. */}
        <div className="mt-3 overflow-x-auto rounded-md bg-surface-container-lowest">
          <div className="relative" style={{ width, height, minHeight: 220 }}>
            <svg
              width={width}
              height={height}
              className="absolute inset-0 text-outline"
              aria-hidden="true"
            >
              <defs>
                <marker
                  id={`${uid}-arrow`}
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path d="M0 0 L10 5 L0 10 z" className="fill-outline" />
                </marker>
              </defs>
              {graph.edges.map((edge, index) => {
                const source = byId(edge.from);
                const target = byId(edge.to);
                if (!source || !target) return null;
                const a = at(source);
                const b = at(target);
                const x1 = a.x + CARD_WIDTH;
                const y1 = a.y + CARD_HEIGHT / 2;
                const x2 = b.x;
                const y2 = b.y + CARD_HEIGHT / 2;
                const mid = (x1 + x2) / 2;
                const bad = error?.edge === index;
                return (
                  <g key={index} className={bad ? "text-error" : "text-outline"}>
                    <path
                      d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={bad ? 2.5 : 1.5}
                      markerEnd={`url(#${uid}-arrow)`}
                    />
                    {edge.when && (
                      <text
                        x={mid}
                        y={(y1 + y2) / 2 - 6}
                        textAnchor="middle"
                        className="fill-on-surface-variant text-[11px]"
                      >
                        {edge.when === "true" ? "참" : "거짓"}
                      </text>
                    )}
                  </g>
                );
              })}
            </svg>

            {graph.nodes.map((node) => {
              const position = at(node);
              const bad = nodeError(node.id);
              const fixed = node.kind === "input" || node.kind === "answer";
              return (
                <div
                  key={node.id}
                  className="absolute"
                  style={{ left: position.x, top: position.y, width: CARD_WIDTH }}
                >
                  <div
                    className={`flex items-start gap-1 rounded-md p-2 ${
                      bad
                        ? "bg-error-container text-on-error-container"
                        : selected === node.id
                          ? "bg-primary-container text-on-primary-container"
                          : "bg-surface-container text-on-surface"
                    }`}
                  >
                    <button
                      type="button"
                      data-node={node.id}
                      onClick={() => {
                        // The click that ends a drag is not a click on the card.
                        if (draggedRef.current) {
                          draggedRef.current = false;
                          return;
                        }
                        setSelected(node.id);
                        setOpenId(node.id);
                      }}
                      onFocus={() => setSelected(node.id)}
                      onKeyDown={(event) => {
                        const step = event.shiftKey ? GRID * 5 : GRID;
                        if (event.key === "ArrowLeft") {
                          event.preventDefault();
                          move(node.id, -step, 0);
                        } else if (event.key === "ArrowRight") {
                          event.preventDefault();
                          move(node.id, step, 0);
                        } else if (event.key === "ArrowUp") {
                          event.preventDefault();
                          move(node.id, 0, -step);
                        } else if (event.key === "ArrowDown") {
                          event.preventDefault();
                          move(node.id, 0, step);
                        } else if ((event.key === "Delete" || event.key === "Backspace") && !fixed) {
                          event.preventDefault();
                          drop(node.id);
                        }
                      }}
                      // Dragging is the bonus route. pointer events rather than
                      // HTML5 drag: a drag image over a canvas of absolutely
                      // positioned cards is a ghost that lands nowhere useful.
                      onPointerDown={(event) => {
                        if (event.button !== 0) return;
                        dragRef.current = {
                          id: node.id,
                          dx: event.clientX - node.x,
                          dy: event.clientY - node.y,
                          moved: false,
                        };
                        event.currentTarget.setPointerCapture(event.pointerId);
                      }}
                      onPointerMove={(event) => {
                        const drag = dragRef.current;
                        if (!drag || drag.id !== node.id) return;
                        drag.moved = true;
                        onChange(
                          updateNode(graph, node.id, {
                            x: Math.round((event.clientX - drag.dx) / GRID) * GRID,
                            y: Math.round((event.clientY - drag.dy) / GRID) * GRID,
                          }),
                        );
                      }}
                      onPointerUp={() => {
                        draggedRef.current = dragRef.current?.moved ?? false;
                        dragRef.current = null;
                      }}
                      className="min-w-0 flex-1 rounded-sm text-left"
                    >
                      <span className="block text-caption">
                        {NODE_KIND_LABEL[node.kind]} · {node.id}
                      </span>
                      <span className="block truncate text-body font-medium">
                        {node.label?.trim() || NODE_KIND_LABEL[node.kind]}
                      </span>
                      <span className="block truncate text-caption opacity-80">
                        {nodeDetail(node)}
                      </span>
                    </button>
                    {!fixed && (
                      <button
                        type="button"
                        onClick={() => drop(node.id)}
                        aria-label={`${node.id} 노드 삭제`}
                        className="icon-btn h-7 w-7 shrink-0"
                      >
                        <svg
                          aria-hidden="true"
                          viewBox="0 0 24 24"
                          className="h-4 w-4"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="1.8"
                          strokeLinecap="round"
                        >
                          <path d="M6 6l12 12M18 6 6 18" />
                        </svg>
                      </button>
                    )}
                  </div>
                  {bad && (
                    <p className="mt-1 rounded-sm bg-error-container px-2 py-1 text-caption text-on-error-container">
                      {bad}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <p role="status" aria-live="polite" className="mt-2 text-caption text-primary">
          {status}
        </p>
      </div>

      {/* The edges, as a list. This is the KEYBOARD route to drawing one, and it
          is also where an edge's own refusal is rendered - a message hung on a
          line in an SVG would be a tooltip nobody can focus. */}
      <div className="rounded-md bg-surface-container-low p-4">
        <h3 className="text-title font-medium">간선</h3>
        <p className="mt-1 text-caption text-on-surface-variant">
          간선이 실행 순서입니다. 분기 노드에서 나가는 간선에는 참·거짓을 반드시 지정해야 합니다.
        </p>

        <div className="mt-3 flex flex-wrap items-end gap-2">
          <div>
            <label htmlFor={`${uid}-from`} className="block text-label text-on-surface-variant">
              시작
            </label>
            <select
              id={`${uid}-from`}
              value={edgeFrom}
              onChange={(event) => setEdgeFrom(event.target.value)}
              className="field mt-1"
            >
              <option value="">고르세요</option>
              {graph.nodes
                .filter((n) => n.kind !== "answer")
                .map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.id} · {n.label?.trim() || NODE_KIND_LABEL[n.kind]}
                  </option>
                ))}
            </select>
          </div>
          <div>
            <label htmlFor={`${uid}-to`} className="block text-label text-on-surface-variant">
              끝
            </label>
            <select
              id={`${uid}-to`}
              value={edgeTo}
              onChange={(event) => setEdgeTo(event.target.value)}
              className="field mt-1"
            >
              <option value="">고르세요</option>
              {graph.nodes
                .filter((n) => n.kind !== "input" && n.id !== edgeFrom)
                .map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.id} · {n.label?.trim() || NODE_KIND_LABEL[n.kind]}
                  </option>
                ))}
            </select>
          </div>
          {fromNode?.kind === "branch" && (
            <div>
              <label htmlFor={`${uid}-when`} className="block text-label text-on-surface-variant">
                조건
              </label>
              <select
                id={`${uid}-when`}
                value={edgeWhen}
                onChange={(event) => setEdgeWhen(event.target.value as "true" | "false")}
                className="field mt-1"
              >
                <option value="true">참</option>
                <option value="false">거짓</option>
              </select>
            </div>
          )}
          <button
            type="button"
            onClick={join}
            disabled={!edgeFrom || !edgeTo}
            className="btn-tonal btn-compact"
          >
            잇기
          </button>
        </div>

        {graph.edges.length === 0 ? (
          <p className="mt-3 text-body text-on-surface-variant">
            간선이 하나도 없습니다. 질문 노드에서 시작해 답변 노드까지 이어야 실행됩니다.
          </p>
        ) : (
          <ul className="mt-3 space-y-1">
            {graph.edges.map((edge, index) => {
              const bad = error?.edge === index;
              return (
                <li
                  key={`${edge.from}-${edge.to}-${edge.when ?? ""}-${index}`}
                  className={`rounded-sm px-3 py-2 ${
                    bad ? "bg-error-container text-on-error-container" : "bg-surface-container"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="min-w-0 truncate text-body">
                      {edge.from} → {edge.to}
                      {edge.when && ` · ${edge.when === "true" ? "참" : "거짓"}`}
                    </span>
                    <button
                      type="button"
                      onClick={() => {
                        onChange(removeEdge(graph, index));
                        setStatus(`${edge.from} → ${edge.to} 간선을 지웠습니다.`);
                      }}
                      className="btn-tonal btn-compact shrink-0"
                    >
                      제거
                    </button>
                  </div>
                  {bad && <p className="mt-1 text-caption">{error?.text}</p>}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {openNode && (
        <NodeDialog
          node={openNode}
          graph={graph}
          callables={callables}
          mcpTools={mcpTools}
          onChange={(patch) => onChange(updateNode(graph, openNode.id, patch))}
          onRemove={() => {
            setOpenId(null);
            drop(openNode.id);
          }}
          onClose={() => {
            setOpenId(null);
            setFocusId(openNode.id);
          }}
        />
      )}
    </div>
  );
}
