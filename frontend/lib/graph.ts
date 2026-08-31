import type { GraphCondition, GraphEdge, GraphNode, WorkflowGraph } from "@/lib/types";

/** The graph editor's rules, with no JSX in them.
 *
 * Same split as lib/mention.ts and for the same reason: this is the part that
 * can be wrong in a way a screenshot would not show - which edge closes a cycle,
 * which node a Korean refusal is about, which references a node is allowed to
 * make - and `node --test` can import it. lib/graph.test.ts is what holds it.
 *
 * NOTHING HERE VALIDATES. `backend/app/workflow/graph.py` is the boundary and it
 * refuses a bad graph at save with a Korean sentence; re-implementing its rules
 * here would produce a second, drifting validator and a screen that disagrees
 * with the server about what is legal. What this file does is put the server's
 * sentence NEXT TO THE THING IT IS ABOUT, which the server cannot do because it
 * has no idea what is on screen.
 */

export const NODE_KIND_LABEL: Record<GraphNode["kind"], string> = {
  input: "질문",
  tool: "도구",
  branch: "분기",
  answer: "답변",
};

/** What a node can be asked for after it has run - `backend/app/workflow/
 * executor.py` writes exactly these into the scope. `items.N.*` is left out of
 * the offered list because N is a number only the run knows; it can still be
 * typed by hand. */
export const NODE_FIELDS = ["count", "text", "top.title", "top.text", "top.ref"];

/** The graph a new workflow starts as: the one that behaves exactly like the
 * direct RAG path. Mirrors STARTER_GRAPH in `backend/app/workflow/router.py`,
 * which is also what migration 0010 wrote for every converted row. A blank
 * canvas would be a workflow that saves and cannot run. */
export function starterGraph(): WorkflowGraph {
  return {
    nodes: [
      { id: "input", kind: "input", label: "질문", x: 0, y: 0 },
      {
        id: "search",
        kind: "tool",
        label: "문서 검색",
        tool: "rag",
        collections: [],
        arguments: { query: "{{input.text}}" },
        x: 260,
        y: 0,
      },
      { id: "answer", kind: "answer", label: "답변", x: 520, y: 0 },
    ],
    edges: [
      { from: "input", to: "search" },
      { from: "search", to: "answer" },
    ],
  };
}

/** `n1`, `n2`, … skipping every id already in use. Ids are what edges and
 * `{{…}}` references name, so they have to be stable and short; the backend
 * accepts Hangul in one but a generated id has no business being clever. */
export function nextNodeId(graph: WorkflowGraph): string {
  const used = new Set(graph.nodes.map((n) => n.id));
  for (let index = 1; ; index += 1) {
    const id = `n${index}`;
    if (!used.has(id)) return id;
  }
}

/** Every node that certainly runs before this one: the transitive sources of its
 * incoming edges.
 *
 * NARROWER THAN THE SERVER ALLOWS, deliberately. `validate_graph` accepts a
 * reference to any node earlier in ONE topological order, which includes nodes
 * on a parallel branch that merely happen to sort first. An ancestor is earlier
 * in every valid order, so offering only ancestors can never produce a graph the
 * server refuses - and a reference to a parallel branch that did not run is a
 * failure waiting for the first question rather than a feature. */
export function ancestorsOf(graph: WorkflowGraph, nodeId: string): string[] {
  const found = new Set<string>();
  const queue = [nodeId];
  while (queue.length > 0) {
    const current = queue.shift() as string;
    for (const edge of graph.edges) {
      if (edge.to !== current || found.has(edge.from)) continue;
      found.add(edge.from);
      queue.push(edge.from);
    }
  }
  found.delete(nodeId);
  return [...found];
}

/** The `{{…}}` a node may legally reference, as pickable values.
 *
 * A reference must be the WHOLE argument value - see `backend/app/workflow/
 * expr.py`, where a template is refused at save - so offering them as a LIST
 * rather than as text to interpolate is not a shortcut: it is the rule, made
 * impossible to break. */
export function referenceOptions(
  graph: WorkflowGraph,
  nodeId: string,
): { value: string; label: string }[] {
  const options: { value: string; label: string }[] = [];
  for (const id of ancestorsOf(graph, nodeId)) {
    const node = graph.nodes.find((n) => n.id === id);
    if (!node) continue;
    if (node.kind === "input") {
      options.push({ value: "{{input.text}}", label: "질문 전체 ({{input.text}})" });
      continue;
    }
    if (node.kind !== "tool") continue;
    const name = node.label?.trim() || id;
    for (const field of NODE_FIELDS) {
      options.push({ value: `{{${id}.${field}}}`, label: `${name} · ${field}` });
    }
  }
  return options;
}

/** The index of an edge that closes a cycle, or null.
 *
 * The server refuses a cycle with 그래프의 간선이 순환합니다. and CANNOT say
 * which edge: it discovers the cycle by failing to make progress in a
 * topological sort, at which point every remaining edge looks equally guilty.
 * A depth-first walk knows - the edge that reaches a node still on the stack is
 * the one that closes it - and that is the edge the message belongs on.
 *
 * ponytail: first back edge found, not the smallest cycle. There is no useful
 * notion of "the" offending edge in a graph with two cycles, and pointing at one
 * real one is what a person needs to start deleting. */
export function cycleEdgeIndex(graph: WorkflowGraph): number | null {
  const state = new Map<string, "open" | "done">();
  let found: number | null = null;

  const walk = (id: string) => {
    if (found !== null) return;
    state.set(id, "open");
    graph.edges.forEach((edge, index) => {
      if (found !== null || edge.from !== id) return;
      const target = state.get(edge.to);
      if (target === "open") {
        found = index;
        return;
      }
      if (target === undefined) walk(edge.to);
    });
    state.set(id, "done");
  };

  for (const node of graph.nodes) {
    if (!state.has(node.id)) walk(node.id);
    if (found !== null) return found;
  }
  return found;
}

/** Where the server's refusal belongs on screen.
 *
 * ONE BANNER AT THE TOP IS THE WRONG ANSWER. "분기 노드의 간선에는 참/거짓을
 * 지정해야 합니다: b1" is a sentence about one edge, and a person reading it
 * above a canvas of eleven boxes has to translate an id back into a picture
 * before they can act. The message is the server's, verbatim - it is never
 * rewritten here - and this only decides WHERE to hang it.
 *
 * The fallback is the banner, and that is honest: 노드가 상한(...)을 넘었습니다
 * is about the whole graph, and so is a missing 질문 node.
 */
export function placeGraphError(
  message: string,
  graph: WorkflowGraph,
): { node?: string; edge?: number; text: string } {
  // The cycle: no name in the message, and the one case where the client knows
  // something the server does not.
  if (message.includes("순환합니다")) {
    const edge = cycleEdgeIndex(graph);
    return edge === null ? { text: message } : { edge, text: message };
  }

  const marker = message.lastIndexOf(": ");
  const tail = marker === -1 ? null : message.slice(marker + 2).trim();

  if (tail) {
    // A `{{…}}` reference: the node is whichever one wrote it.
    if (tail.startsWith("{{")) {
      const node = graph.nodes.find(
        (n) =>
          JSON.stringify(n.arguments ?? {}).includes(tail) ||
          JSON.stringify(n.condition ?? {}).includes(tail),
      );
      if (node) return { node: node.id, text: message };
    }
    // Every message that names an edge names it by an ENDPOINT id, so the
    // 간선 in the sentence is what tells the two apart: `b1` in a branch-edge
    // message is the edge's SOURCE, not the node's own mistake. Source before
    // target, because every one of those messages is about an edge LEAVING the
    // node it names - matching the target first put 분기 노드의 간선에는 참/거짓을
    // on the edge arriving at the branch, which is the one edge that is fine.
    if (message.includes("간선")) {
      // Two of them are about an edge's `when`, and a branch usually has one
      // good edge and one bad one. Narrow to the bad one rather than pointing at
      // whichever was drawn first.
      const wants =
        message.includes("지정해야") ? "missing" : message.includes("지정할 수 없") ? "present" : "any";
      const offending = (e: GraphEdge) =>
        wants === "missing" ? e.when === undefined : wants === "present" ? e.when !== undefined : true;
      const fromIndex = graph.edges.findIndex((e) => e.from === tail && offending(e));
      if (fromIndex !== -1) return { edge: fromIndex, text: message };
      const index = graph.edges.findIndex((e) => e.from === tail || e.to === tail);
      if (index !== -1) return { edge: index, text: message };
    }
    const byId = graph.nodes.find((n) => n.id === tail);
    if (byId) return { node: byId.id, text: message };
    // A tool, a workflow or a collection the graph names but the catalogue does
    // not have.
    const byName = graph.nodes.find(
      (n) =>
        n.tool === tail ||
        n.tool === `mcp:${tail}` ||
        n.tool === `workflow:${tail}` ||
        (n.collections ?? []).includes(tail),
    );
    if (byName) return { node: byName.id, text: message };
  }

  if (message.includes("검색어가 없는")) {
    const node = graph.nodes.find(
      (n) =>
        n.kind === "tool" &&
        (n.tool ?? "rag") === "rag" &&
        !String((n.arguments as { query?: unknown } | undefined)?.query ?? "").trim(),
    );
    if (node) return { node: node.id, text: message };
  }

  // A condition the evaluator would not understand carries no id at all. With
  // one branch node on the canvas there is no ambiguity about which it is;
  // with two there is, and guessing would point at the innocent one.
  if (message.includes("분기") || message.includes("조건")) {
    const branches = graph.nodes.filter((n) => n.kind === "branch");
    if (branches.length === 1) return { node: branches[0].id, text: message };
  }

  return { text: message };
}

/** A node added to the canvas, in front of the 답변 node rather than after it.
 *
 * Appending at max-x is what the first version did, and the picture it produced
 * was a lie by layout: 답변 sat in the middle of the row with two later nodes to
 * its right and an edge running backwards over them. The edges were right and
 * the drawing was wrong, which is the one failure a canvas cannot afford. So a
 * new node takes 답변's column and pushes 답변 - and anything at or beyond it -
 * one column right. The person can still drag it anywhere; this is only where it
 * lands. */
export function addNode(graph: WorkflowGraph, kind: GraphNode["kind"]): WorkflowGraph {
  const id = nextNodeId(graph);
  const answer = graph.nodes.find((n) => n.kind === "answer");
  const x = answer ? answer.x : graph.nodes.reduce((max, n) => Math.max(max, n.x), 0) + 260;
  const nodes = answer
    ? graph.nodes.map((n) => (n.x >= x ? { ...n, x: n.x + 260 } : n))
    : [...graph.nodes];
  const node: GraphNode =
    kind === "branch"
      ? {
          id,
          kind,
          label: "",
          x,
          y: 0,
          condition: { kind: "exists", of: "" },
        }
      : { id, kind, label: "", x, y: 0, tool: "rag", collections: [], arguments: { query: "" } };
  return { nodes: [...nodes, node], edges: graph.edges };
}

/** Removing a node removes every edge that touched it. Leaving them would be a
 * graph the server refuses with 존재하지 않는 노드를 잇는 간선 - a refusal the
 * user did nothing to earn. */
export function removeNode(graph: WorkflowGraph, id: string): WorkflowGraph {
  return {
    nodes: graph.nodes.filter((n) => n.id !== id),
    edges: graph.edges.filter((e) => e.from !== id && e.to !== id),
  };
}

export function updateNode(graph: WorkflowGraph, id: string, patch: Partial<GraphNode>): WorkflowGraph {
  return {
    nodes: graph.nodes.map((n) => (n.id === id ? { ...n, ...patch } : n)),
    edges: graph.edges,
  };
}

/** An edge, unless the identical one is already there. A duplicate would not be
 * refused by the server - it is just a second line drawn over the first, which
 * is a picture nobody can then delete the right half of. */
export function addEdge(graph: WorkflowGraph, edge: GraphEdge): WorkflowGraph {
  const exists = graph.edges.some(
    (e) => e.from === edge.from && e.to === edge.to && (e.when ?? null) === (edge.when ?? null),
  );
  return exists ? graph : { nodes: graph.nodes, edges: [...graph.edges, edge] };
}

export function removeEdge(graph: WorkflowGraph, index: number): WorkflowGraph {
  return { nodes: graph.nodes, edges: graph.edges.filter((_, i) => i !== index) };
}

/** One condition, described in Korean, for the card and the edge list. The
 * canvas renders `{"kind":"compare","left":"{{n1.count}}","op":">","right":0}`
 * as `{{n1.count}} > 0` - the shape the spec asked for. */
export function conditionText(condition: GraphCondition | null | undefined): string {
  if (!condition) return "조건 없음";
  if (condition.kind === "compare") {
    return `${String(condition.left ?? "")} ${condition.op ?? ""} ${JSON.stringify(condition.right ?? null)}`;
  }
  if (condition.kind === "exists") return `${String(condition.of ?? "")} 있음`;
  if (condition.kind === "empty") return `${String(condition.of ?? "")} 비어 있음`;
  if (condition.kind === "not") return `아님(${conditionText(condition.of as GraphCondition)})`;
  if (condition.kind === "and" || condition.kind === "or") {
    const parts = Array.isArray(condition.of) ? (condition.of as GraphCondition[]) : [];
    return parts.map(conditionText).join(condition.kind === "and" ? " 그리고 " : " 또는 ");
  }
  return "모델 판단";
}
