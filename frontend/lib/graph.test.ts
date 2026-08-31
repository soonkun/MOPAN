// Run: npm test
//
// The two things in the graph editor that a screenshot cannot check: which edge
// closes a cycle, and where a Korean refusal from the server belongs. Both are
// the difference between "the message is on screen" and "the message is on the
// thing it is about", which is what this slice was asked for.
//
// The messages below are COPIED from backend/app/workflow/graph.py. If one is
// reworded there, a test here fails - which is the point: this file is the only
// place the two sides are written down together.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  addEdge,
  addNode,
  ancestorsOf,
  conditionText,
  cycleEdgeIndex,
  nextNodeId,
  placeGraphError,
  referenceOptions,
  removeNode,
  starterGraph,
} from "./graph.ts";
import type { WorkflowGraph } from "./types.ts";

/** input -> n1 -> b1 -{true}-> n2 -> answer, b1 -{false}-> n3 -> answer */
function branching(): WorkflowGraph {
  return {
    nodes: [
      { id: "input", kind: "input", x: 0, y: 0 },
      { id: "n1", kind: "tool", label: "문서 검색", tool: "rag", arguments: { query: "{{input.text}}" }, x: 260, y: 0 },
      {
        id: "b1",
        kind: "branch",
        x: 520,
        y: 0,
        condition: { kind: "compare", left: "{{n1.count}}", op: ">", right: 0 },
      },
      { id: "n2", kind: "tool", tool: "rag", arguments: { query: "{{n1.top.title}}" }, x: 780, y: -100 },
      { id: "n3", kind: "tool", tool: "rag", arguments: { query: "{{input.text}}" }, x: 780, y: 100 },
      { id: "answer", kind: "answer", x: 1040, y: 0 },
    ],
    edges: [
      { from: "input", to: "n1" },
      { from: "n1", to: "b1" },
      { from: "b1", to: "n2", when: "true" },
      { from: "b1", to: "n3", when: "false" },
      { from: "n2", to: "answer" },
      { from: "n3", to: "answer" },
    ],
  };
}

test("a graph with no cycle has no offending edge", () => {
  assert.equal(cycleEdgeIndex(branching()), null);
  assert.equal(cycleEdgeIndex(starterGraph()), null);
});

test("the edge that closes the cycle is the one named", () => {
  const graph = addEdge(branching(), { from: "n2", to: "n1" });
  const index = cycleEdgeIndex(graph);
  assert.equal(index, graph.edges.length - 1);
  assert.deepEqual(graph.edges[index as number], { from: "n2", to: "n1" });
});

test("a cycle refusal is placed on that edge, with the server's words", () => {
  const graph = addEdge(branching(), { from: "n2", to: "n1" });
  const placed = placeGraphError("그래프의 간선이 순환합니다.", graph);
  assert.equal(placed.edge, 6);
  assert.equal(placed.node, undefined);
  assert.equal(placed.text, "그래프의 간선이 순환합니다.");
});

test("a branch-edge refusal lands on the edge, not on the branch node", () => {
  const graph = branching();
  graph.edges[2] = { from: "b1", to: "n2" };
  const placed = placeGraphError("분기 노드의 간선에는 참/거짓을 지정해야 합니다: b1", graph);
  assert.equal(placed.edge, 2);
});

test("a refusal that names a node lands on that node", () => {
  const graph = branching();
  assert.equal(
    placeGraphError("조건이 없는 분기 노드가 있습니다: b1", graph).node,
    "b1",
  );
  assert.equal(
    placeGraphError("등록되지 않은 도구를 지정한 그래프입니다: 현장관측/water", {
      nodes: [{ id: "n7", kind: "tool", tool: "mcp:현장관측/water", x: 0, y: 0 }],
      edges: [],
    }).node,
    "n7",
  );
});

test("a forward reference lands on the node that wrote it", () => {
  const placed = placeGraphError("앞서 실행되지 않는 노드를 참조합니다: {{n1.top.title}}", branching());
  assert.equal(placed.node, "n2");
});

test("a graph-wide refusal stays a banner", () => {
  const placed = placeGraphError("노드가 상한(20개)을 넘었습니다.", branching());
  assert.deepEqual(placed, { text: "노드가 상한(20개)을 넘었습니다." });
});

test("a condition refusal with no id is placed only when there is one branch", () => {
  const one = branching();
  assert.equal(placeGraphError("분기 조건의 모양이 올바르지 않습니다.", one).node, "b1");
  const two = addEdge(
    { nodes: [...one.nodes, { id: "b2", kind: "branch", x: 0, y: 0 }], edges: one.edges },
    { from: "n1", to: "b2" },
  );
  assert.equal(placeGraphError("분기 조건의 모양이 올바르지 않습니다.", two).node, undefined);
});

test("only ancestors are offered as references", () => {
  const graph = branching();
  assert.deepEqual(ancestorsOf(graph, "n2").sort(), ["b1", "input", "n1"]);
  // n3 runs on the other side of the branch, so it is never offered to n2 -
  // even though the server's topological order might place it first.
  const offered = referenceOptions(graph, "n2").map((o) => o.value);
  assert.ok(offered.includes("{{input.text}}"));
  assert.ok(offered.includes("{{n1.count}}"));
  assert.ok(!offered.some((value) => value.startsWith("{{n3.")));
});

test("a new node lands in front of 답변, not after it", () => {
  // Appending at max-x put 답변 in the middle of the row with the new node to
  // its right, and the edge into it ran backwards across the picture.
  const graph = addNode(starterGraph(), "tool");
  const byId = Object.fromEntries(graph.nodes.map((n) => [n.id, n.x]));
  assert.equal(byId.n1, 520);
  assert.equal(byId.answer, 780);
  assert.equal(byId.search, 260);
});

test("removing a node removes the edges that touched it", () => {
  const graph = removeNode(branching(), "n2");
  assert.deepEqual(
    graph.edges.map((e) => `${e.from}->${e.to}`),
    ["input->n1", "n1->b1", "b1->n3", "n3->answer"],
  );
});

test("an identical edge is not added twice", () => {
  const graph = branching();
  assert.equal(addEdge(graph, { from: "input", to: "n1" }).edges.length, graph.edges.length);
  assert.equal(addEdge(graph, { from: "input", to: "b1" }).edges.length, graph.edges.length + 1);
});

test("node ids skip what is taken", () => {
  assert.equal(nextNodeId(branching()), "n4");
});

test("a condition renders the way the spec writes it", () => {
  assert.equal(
    conditionText({ kind: "compare", left: "{{n1.count}}", op: ">", right: 0 }),
    "{{n1.count}} > 0",
  );
  assert.equal(conditionText({ kind: "empty", of: "{{n1.text}}" }), "{{n1.text}} 비어 있음");
  assert.equal(conditionText(null), "조건 없음");
});
