"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { NODE_KIND_LABEL, addEdge, addNode, removeEdge, removeNode, updateNode } from "@/lib/graph";
import { nodeDetail } from "@/components/workflows/fields";
import Palette from "@/components/workflows/Palette";
import type { CallableTool, GraphNode, WorkflowGraph } from "@/lib/types";

/** The canvas, and nothing but the canvas.
 *
 * THE CANVAS IS THE SCREEN NOW. The first editor was a 220px strip inside a
 * scrolling form, edges were drawn from two <select>s in a card below it, and
 * the permission boundary had a second canvas of its own - the tools filled the
 * room and the drawing got what was left. This component owns the whole
 * viewport; every form control lives in the Inspector beside it.
 *
 * STILL NO GRAPH LIBRARY. What changed is the job: pan/zoom and drag-to-connect
 * ARE the job now, and they cost ~120 lines here against react-flow's ~50KB and
 * five dependencies. The primitives stayed the same - absolutely positioned
 * cards over one <svg> of paths.
 *
 * DRAG IS STILL NEVER THE ONLY ROUTE. Every node is a <button>: Enter opens its
 * settings in the Inspector, the arrow keys move it, Delete removes it. An edge
 * can be drawn by dragging port to port, and exactly the same edge can be made
 * from the Inspector's 간선 section with two selects - the keyboard path the
 * first editor promised is kept, it just moved out of the canvas's way.
 *
 * THE SERVER'S REFUSAL GOES WHERE THE MISTAKE IS, verbatim, as before: node
 * refusals under the card, edge refusals on a chip at the edge's midpoint.
 */

export const CARD_WIDTH = 200;
const CARD_HEIGHT = 84;
const GRID = 20;
const MIN_SCALE = 0.25;
const MAX_SCALE = 2;

export type Selection = { node: string } | { edge: number } | null;

type View = { tx: number; ty: number; scale: number };

// 세로 흐름: 들어오는 포트는 위 가운데, 나가는 포트는 아래 가운데. 가로는
// "보기에도 별로고 문제가 많다"는 소유자 지적으로 뒤집었다 - 위에서 아래로
// 읽는 그래프가 좁은 화면에서도 산다.
function portCenter(node: GraphNode, side: "in" | "out") {
  return {
    x: node.x + CARD_WIDTH / 2,
    y: node.y + (side === "out" ? CARD_HEIGHT : 0),
  };
}

/** 분기에서 나가는 간선의 조건 기본값: 참이 비어 있으면 참, 아니면 거짓.
 * 두 간선을 그리는 사람이 조건을 한 번도 안 고르고도 맞는 그래프가 되게. */
export function defaultWhen(
  graph: WorkflowGraph,
  from: string,
): "true" | "false" | undefined {
  const source = graph.nodes.find((n) => n.id === from);
  if (source?.kind !== "branch") return undefined;
  const hasTrue = graph.edges.some((e) => e.from === from && e.when === "true");
  return hasTrue ? "false" : "true";
}

export default function EditorCanvas({
  graph,
  onChange,
  selection,
  onSelect,
  error,
  callables,
  paletteOpen,
  onPaletteOpenChange,
  runStates,
}: {
  graph: WorkflowGraph;
  onChange: (next: WorkflowGraph) => void;
  selection: Selection;
  onSelect: (next: Selection) => void;
  /** placeGraphError가 이미 자리를 정한 서버의 거절. */
  error: { node?: string; edge?: number; text: string } | null;
  callables: CallableTool[];
  /** 페이지의 왼쪽 레일이 토글한다 - 처음 화면 맞추기가 서랍 폭을 알아야
   * 해서 값은 여전히 캔버스로 들어온다. */
  paletteOpen: boolean;
  onPaletteOpenChange: (open: boolean) => void;
  /** 실행해보기의 노드별 진행 상태(step 프레임의 id -> state). 실행 중이 아니면
   * 비어 있고, 그때 캔버스는 이전과 픽셀 단위로 같다. */
  runStates?: Record<string, string>;
}) {
  const uid = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<View>({ tx: 60, ty: 60, scale: 1 });
  const viewRef = useRef(view);
  viewRef.current = view;
  // 서랍은 캔버스가 들고 있다: 처음 화면 맞추기가 서랍이 가리는 폭을 빼고
  // 계산해야 질문 노드가 서랍 뒤에서 시작하지 않는다. 390px에서는 서랍이
  // 화면 전체를 먹으므로 접힌 채로 시작한다.
  const paletteInset = paletteOpen ? 264 : 0;
  const [status, setStatus] = useState("");
  const [focusId, setFocusId] = useState<string | null>(null);

  // 노드 드래그. moved가 드래그와 클릭을 가른다 - 버튼 위에서 눌렀다 떼면
  // 클릭이 따라오므로, 이것 없이는 모든 드래그가 설정을 열면서 끝난다.
  const dragRef = useRef<{ id: string; dx: number; dy: number; moved: boolean } | null>(null);
  const draggedRef = useRef(false);
  // 빈 캔버스 팬.
  const panRef = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  // 포트에서 시작한 연결 프리뷰. 좌표는 월드 좌표.
  const [connect, setConnect] = useState<{ from: string; x: number; y: number } | null>(null);

  useEffect(() => {
    if (!focusId) return;
    rootRef.current?.querySelector<HTMLElement>(`[data-node="${CSS.escape(focusId)}"]`)?.focus();
    setFocusId(null);
  }, [focusId, graph]);

  const byId = useCallback(
    (id: string) => graph.nodes.find((n) => n.id === id),
    [graph.nodes],
  );

  /** 뷰포트 픽셀 -> 월드 좌표. 팬/줌이 있는 순간부터 clientX를 그대로 쓰면
   * 노드가 커서에서 도망간다. */
  function worldPoint(clientX: number, clientY: number) {
    const rect = rootRef.current?.getBoundingClientRect();
    const { tx, ty, scale } = viewRef.current;
    return {
      x: ((clientX - (rect?.left ?? 0)) - tx) / scale,
      y: ((clientY - (rect?.top ?? 0)) - ty) / scale,
    };
  }

  const fit = useCallback(() => {
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect || graph.nodes.length === 0) return;
    const minX = Math.min(...graph.nodes.map((n) => n.x));
    const minY = Math.min(...graph.nodes.map((n) => n.y));
    const maxX = Math.max(...graph.nodes.map((n) => n.x + CARD_WIDTH));
    const maxY = Math.max(...graph.nodes.map((n) => n.y + CARD_HEIGHT));
    const pad = 80;
    // 서랍이 가리는 왼쪽 폭은 없는 셈 친다.
    const width = rect.width - paletteInset;
    const fitScale = Math.min(
      (width - pad) / (maxX - minX || 1),
      (rect.height - pad) / (maxY - minY || 1),
      1,
    );
    if (fitScale >= 0.6) {
      setView({
        tx: paletteInset + (width - (maxX - minX) * fitScale) / 2 - minX * fitScale,
        ty: (rect.height - (maxY - minY) * fitScale) / 2 - minY * fitScale,
        scale: fitScale,
      });
      return;
    }
    // 좁은 화면에서 통째로 맞추면 카드가 읽을 수 없게 작아진다 (1440px에서
    // 사이드바+서랍+인스펙터를 빼면 44%까지 떨어진 실측). 읽히는 배율로
    // 흐름의 시작을 왼쪽에 앵커하고, 나머지는 팬이 있다.
    const scale = 0.75;
    setView({
      tx: paletteInset + 40 - minX * scale,
      ty: (rect.height - (maxY - minY) * scale) / 2 - minY * scale,
      scale,
    });
  }, [graph.nodes, paletteInset]);

  // 처음 열릴 때 한 번 그래프를 화면에 맞춘다. 이후에는 사람이 잡은 시점을
  // 존중한다 - 노드를 추가할 때마다 화면이 튀면 그리는 맛이 없다.
  const fitted = useRef(false);
  useEffect(() => {
    if (fitted.current) return;
    fitted.current = true;
    fit();
  }, [fit]);

  function zoomAt(clientX: number, clientY: number, factor: number) {
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect) return;
    setView((prev) => {
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, prev.scale * factor));
      if (scale === prev.scale) return prev;
      const px = clientX - rect.left;
      const py = clientY - rect.top;
      // 커서 아래 월드 점이 줌 후에도 커서 아래 있도록.
      return {
        scale,
        tx: px - ((px - prev.tx) / prev.scale) * scale,
        ty: py - ((py - prev.ty) / prev.scale) * scale,
      };
    });
  }

  function move(id: string, dx: number, dy: number) {
    const node = byId(id);
    if (!node) return;
    onChange(updateNode(graph, id, { x: node.x + dx, y: node.y + dy }));
  }

  function drop(id: string) {
    onChange(removeNode(graph, id));
    onSelect(null);
    setStatus(`${id} 노드와 그 노드에 붙어 있던 간선을 지웠습니다.`);
  }

  function finishConnect(clientX: number, clientY: number) {
    const from = connect?.from;
    setConnect(null);
    if (!from) return;
    const target = (document.elementFromPoint(clientX, clientY) as HTMLElement | null)
      ?.closest<HTMLElement>("[data-node]")
      ?.dataset.node;
    if (!target || target === from) return;
    const destination = byId(target);
    if (!destination || destination.kind === "input") return;
    const when = defaultWhen(graph, from);
    onChange(addEdge(graph, { from, to: target, ...(when ? { when } : {}) }));
    setStatus(
      `${from} → ${target} 간선을 그었습니다.${when ? ` (${when === "true" ? "참" : "거짓"})` : ""}`,
    );
  }

  /** 팔레트에서 온 추가. at이 있으면(끌어다 놓기) 그 자리, 없으면(클릭)
   * 지금 보이는 화면의 가운데 - 캔버스 밖 어딘가에 생긴 노드를 찾아
   * 헤매게 하지 않는다. */
  function addFromPalette(
    kind: GraphNode["kind"],
    tool?: string,
    at?: { clientX: number; clientY: number },
  ) {
    let x: number;
    let y: number;
    if (at) {
      const point = worldPoint(at.clientX, at.clientY);
      x = point.x - CARD_WIDTH / 2;
      y = point.y - CARD_HEIGHT / 2;
    } else {
      const rect = rootRef.current?.getBoundingClientRect();
      const centre = worldPoint(
        (rect?.left ?? 0) + (rect?.width ?? 600) / 2,
        (rect?.top ?? 0) + (rect?.height ?? 400) / 2,
      );
      x = centre.x - CARD_WIDTH / 2;
      y = centre.y - CARD_HEIGHT / 2;
      // 같은 자리에 연달아 놓으면 카드가 포개져 하나로 보인다.
      while (graph.nodes.some((n) => Math.abs(n.x - x) < GRID && Math.abs(n.y - y) < GRID)) {
        x += GRID * 2;
        y += GRID * 2;
      }
    }
    const next = addNode(graph, kind, {
      x: Math.round(x / GRID) * GRID,
      y: Math.round(y / GRID) * GRID,
      ...(tool !== undefined ? { tool } : {}),
    });
    const node = next.nodes[next.nodes.length - 1];
    onChange(next);
    onSelect({ node: node.id });
    setFocusId(node.id);
    setStatus(`${NODE_KIND_LABEL[kind]} 노드 ${node.id}을(를) 추가했습니다. 오른쪽 설정에서 채워 주세요.`);
  }

  const nodeError = (id: string) => (error?.node === id ? error.text : null);
  const selectedNode = selection && "node" in selection ? selection.node : null;
  const selectedEdge = selection && "edge" in selection ? selection.edge : null;
  const connectSource = connect ? byId(connect.from) : null;

  return (
    <div
      ref={rootRef}
      className="relative h-full min-h-0 w-full touch-none overflow-hidden bg-surface-container-lowest"
      style={{
        backgroundImage: "radial-gradient(var(--outline-variant) 1px, transparent 1px)",
        backgroundSize: `${24 * view.scale}px ${24 * view.scale}px`,
        backgroundPosition: `${view.tx}px ${view.ty}px`,
      }}
      onPointerDown={(event) => {
        // 빈 바닥에서만 팬을 시작한다. 카드·포트는 자기 핸들러가 stopPropagation.
        if (event.target !== event.currentTarget || event.button !== 0) return;
        panRef.current = { x: event.clientX, y: event.clientY, tx: view.tx, ty: view.ty };
        event.currentTarget.setPointerCapture(event.pointerId);
        onSelect(null);
      }}
      onPointerMove={(event) => {
        const pan = panRef.current;
        if (pan) {
          setView((prev) => ({
            ...prev,
            tx: pan.tx + (event.clientX - pan.x),
            ty: pan.ty + (event.clientY - pan.y),
          }));
        }
        if (connect) {
          const point = worldPoint(event.clientX, event.clientY);
          setConnect({ ...connect, x: point.x, y: point.y });
        }
      }}
      onPointerUp={(event) => {
        panRef.current = null;
        if (connect) finishConnect(event.clientX, event.clientY);
      }}
      onWheel={(event) => {
        if (event.ctrlKey || event.metaKey) {
          event.preventDefault();
          zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 1.12 : 1 / 1.12);
        } else {
          setView((prev) => ({ ...prev, tx: prev.tx - event.deltaX, ty: prev.ty - event.deltaY }));
        }
      }}
      onKeyDown={(event) => {
        if (
          selectedEdge !== null &&
          (event.key === "Delete" || event.key === "Backspace") &&
          event.target === event.currentTarget
        ) {
          const edge = graph.edges[selectedEdge];
          if (edge) {
            onChange(removeEdge(graph, selectedEdge));
            onSelect(null);
            setStatus(`${edge.from} → ${edge.to} 간선을 지웠습니다.`);
          }
        }
      }}
      onDragOver={(event) => {
        if (
          event.dataTransfer.types.includes("application/x-mopan-node") ||
          event.dataTransfer.types.includes("application/x-mopan-tool")
        ) {
          event.preventDefault();
          event.dataTransfer.dropEffect = "copy";
        }
      }}
      onDrop={(event) => {
        const kind = event.dataTransfer.getData("application/x-mopan-node");
        const tool = event.dataTransfer.getData("application/x-mopan-tool");
        if (!kind && !tool) return;
        event.preventDefault();
        addFromPalette(
          (kind || "tool") as GraphNode["kind"],
          tool || undefined,
          { clientX: event.clientX, clientY: event.clientY },
        );
      }}
      tabIndex={-1}
      role="application"
      aria-label="워크플로우 캔버스"
    >
      <div
        className="absolute left-0 top-0"
        style={{
          transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.scale})`,
          transformOrigin: "0 0",
        }}
      >
        <svg
          width={1}
          height={1}
          className="absolute left-0 top-0 overflow-visible text-outline"
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
            <marker
              id={`${uid}-arrow-active`}
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M0 0 L10 5 L0 10 z" className="fill-primary" />
            </marker>
          </defs>
          {graph.edges.map((edge, index) => {
            const source = byId(edge.from);
            const target = byId(edge.to);
            if (!source || !target) return null;
            const a = portCenter(source, "out");
            const b = portCenter(target, "in");
            const mid = (a.y + b.y) / 2;
            const path = `M ${a.x} ${a.y} C ${a.x} ${mid}, ${b.x} ${mid}, ${b.x} ${b.y}`;
            const bad = error?.edge === index;
            const active = selectedEdge === index;
            return (
              <g
                key={index}
                className={bad ? "text-error" : active ? "text-primary" : "text-outline"}
              >
                {/* 두꺼운 투명 스트로크가 클릭 판정. 1.5px 선을 조준해서 맞히는
                    사람은 없다. */}
                <path
                  d={path}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={14}
                  className="cursor-pointer"
                  style={{ pointerEvents: "stroke" }}
                  onPointerDown={(event) => {
                    event.stopPropagation();
                    onSelect({ edge: index });
                    rootRef.current?.focus();
                  }}
                />
                <path
                  d={path}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={bad ? 2.5 : active ? 2.5 : 1.5}
                  markerEnd={`url(#${uid}-arrow${active ? "-active" : ""})`}
                  style={{ pointerEvents: "none" }}
                />
                {edge.when && (
                  <text
                    x={(a.x + b.x) / 2 + 12}
                    y={mid + 4}
                    textAnchor="start"
                    className="fill-on-surface-variant text-[11px]"
                    style={{ pointerEvents: "none" }}
                  >
                    {edge.when === "true" ? "참" : "거짓"}
                  </text>
                )}
                {bad && (
                  <foreignObject
                    x={(a.x + b.x) / 2 + 12}
                    y={mid + 12}
                    width={260}
                    height={80}
                    style={{ pointerEvents: "none" }}
                  >
                    <p className="rounded-sm bg-error-container px-2 py-1 text-caption text-on-error-container">
                      {error?.text}
                    </p>
                  </foreignObject>
                )}
              </g>
            );
          })}
          {connect && connectSource && (
            <path
              d={(() => {
                const a = portCenter(connectSource, "out");
                const mid = (a.y + connect.y) / 2;
                return `M ${a.x} ${a.y} C ${a.x} ${mid}, ${connect.x} ${mid}, ${connect.x} ${connect.y}`;
              })()}
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              strokeDasharray="6 4"
              className="text-primary"
              style={{ pointerEvents: "none" }}
            />
          )}
        </svg>

        {graph.nodes.map((node) => {
          const bad = nodeError(node.id);
          const fixed = node.kind === "input" || node.kind === "answer";
          const active = selectedNode === node.id;
          // 실행해보기 점등: running은 숨쉬는 테두리, done은 켜진 테두리,
          // 실패 계열은 error 테두리, 분기가 거른 노드(skipped)는 어두워진다 -
          // "거치지 않은 경로는 켜지지 말고"(소유자). 상태가 없으면 빈 문자열이라
          // 실행 중이 아닐 때 이 줄은 아무것도 바꾸지 않는다.
          const run = runStates?.[node.id];
          const runClass =
            run === "running"
              ? "ring-2 ring-primary run-glow"
              : run === "done"
                ? "ring-2 ring-primary"
                : run === "failed" || run === "timeout"
                  ? "ring-2 ring-error"
                  : run === "skipped"
                    ? "opacity-40"
                    : "";
          return (
            <div
              key={node.id}
              className="absolute"
              style={{ left: node.x, top: node.y, width: CARD_WIDTH }}
            >
              <div
                className={`relative flex items-start gap-1 rounded-md p-2 transition-all duration-150 ${
                  bad
                    ? "bg-error-container text-on-error-container"
                    : active
                      ? "bg-primary-container text-on-primary-container ring-2 ring-primary"
                      : "bg-surface-container text-on-surface hover:bg-surface-container-high"
                } ${runClass}`}
                style={{ minHeight: CARD_HEIGHT }}
              >
                <button
                  type="button"
                  data-node={node.id}
                  onClick={() => {
                    if (draggedRef.current) {
                      draggedRef.current = false;
                      return;
                    }
                    onSelect({ node: node.id });
                  }}
                  onFocus={() => onSelect({ node: node.id })}
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
                  onPointerDown={(event) => {
                    if (event.button !== 0) return;
                    event.stopPropagation();
                    const point = worldPoint(event.clientX, event.clientY);
                    dragRef.current = {
                      id: node.id,
                      dx: point.x - node.x,
                      dy: point.y - node.y,
                      moved: false,
                    };
                    event.currentTarget.setPointerCapture(event.pointerId);
                  }}
                  onPointerMove={(event) => {
                    const drag = dragRef.current;
                    if (!drag || drag.id !== node.id) return;
                    drag.moved = true;
                    const point = worldPoint(event.clientX, event.clientY);
                    onChange(
                      updateNode(graph, node.id, {
                        x: Math.round((point.x - drag.dx) / GRID) * GRID,
                        y: Math.round((point.y - drag.dy) / GRID) * GRID,
                      }),
                    );
                  }}
                  onPointerUp={() => {
                    draggedRef.current = dragRef.current?.moved ?? false;
                    dragRef.current = null;
                  }}
                  className="min-w-0 flex-1 rounded-sm text-left"
                >
                  <span className="block text-caption opacity-80">
                    {NODE_KIND_LABEL[node.kind]} · {node.id}
                  </span>
                  <span className="block truncate text-body font-medium">
                    {node.label?.trim() || NODE_KIND_LABEL[node.kind]}
                  </span>
                  <span className="block truncate text-caption opacity-80">{nodeDetail(node)}</span>
                </button>

                {/* 들어오는 포트. 시각적 목표일 뿐 - 드롭 판정은 카드 전체가 받는다. */}
                {node.kind !== "input" && (
                  <span
                    aria-hidden="true"
                    className={`absolute -top-[7px] left-1/2 h-3.5 w-3.5 -translate-x-1/2 rounded-full border-2 border-outline bg-surface ${
                      connect && connect.from !== node.id ? "border-primary" : ""
                    }`}
                  />
                )}
                {/* 나가는 포트 - 여기서 끌면 간선이 그려진다. */}
                {node.kind !== "answer" && (
                  <button
                    type="button"
                    aria-label={`${node.id}에서 간선 끌기 (키보드는 오른쪽 설정의 간선 절에서)`}
                    className="absolute -bottom-[9px] left-1/2 h-[18px] w-[18px] -translate-x-1/2 cursor-crosshair rounded-full border-2 border-outline bg-surface hover:border-primary hover:bg-primary-container"
                    onPointerDown={(event) => {
                      if (event.button !== 0) return;
                      event.stopPropagation();
                      const point = worldPoint(event.clientX, event.clientY);
                      setConnect({ from: node.id, x: point.x, y: point.y });
                      // 캡처는 캔버스가 가져간다 - 프리뷰와 드롭 판정이 거기 있다.
                      rootRef.current?.setPointerCapture(event.pointerId);
                    }}
                  />
                )}

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

      <Palette
        callables={callables}
        onAdd={addFromPalette}
        open={paletteOpen}
        onOpenChange={onPaletteOpenChange}
      />

      {/* 줌 콘트롤. */}
      <div className="absolute bottom-4 right-4 flex items-center gap-1 rounded-md bg-surface-container p-1 shadow-menu">
        <button
          type="button"
          onClick={() => {
            const rect = rootRef.current?.getBoundingClientRect();
            if (rect) zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, 1 / 1.2);
          }}
          className="icon-btn h-8 w-8"
          aria-label="축소"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M5 12h14" />
          </svg>
        </button>
        <button
          type="button"
          onClick={fit}
          className="h-8 rounded-sm px-2 text-caption text-on-surface-variant hover:bg-surface-container-high"
          aria-label="화면에 맞추기"
        >
          {Math.round(view.scale * 100)}%
        </button>
        <button
          type="button"
          onClick={() => {
            const rect = rootRef.current?.getBoundingClientRect();
            if (rect) zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, 1.2);
          }}
          className="icon-btn h-8 w-8"
          aria-label="확대"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
      </div>

      <p role="status" aria-live="polite" className="sr-only">
        {status}
      </p>
    </div>
  );
}
