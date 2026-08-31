// Run: npm test  (node --test --experimental-strip-types lib/*.test.ts)
//
// Same shape as api.test.ts and for the same reason: no runner, no jsdom, no
// dependency. `mention.ts` imports nothing but a TYPE, which stripping erases,
// so this file runs against the shipped code rather than a copy of it.
//
// What it covers is the part of `@` that has rules rather than markup: where a
// token starts and ends - the email case is a real question a user types - and
// the expansion of ONE `rag` entry into one row per collection, which is the
// only place the menu invents rows the API did not send.
import { test } from "node:test";
import assert from "node:assert/strict";

import { filterEntries, mentionAt, mentionEntries } from "./mention.ts";

test("mentionAt finds the token the caret is in", () => {
  assert.deepEqual(mentionAt("@", 1), { start: 0, query: "" });
  assert.deepEqual(mentionAt("논이 @특허", 6), { start: 3, query: "특허" });
  assert.deepEqual(mentionAt("논이 @특허", 4), { start: 3, query: "" });
});

test("mentionAt refuses what is not an invocation", () => {
  // An email address: the `@` follows a letter, not whitespace.
  assert.equal(mentionAt("a@b.com", 7), null);
  // The caret has walked past the token.
  assert.equal(mentionAt("@검색 그리고", 6), null);
  // Two `@` in a row is not a name.
  assert.equal(mentionAt("@@", 2), null);
  assert.equal(mentionAt("질문", null), null);
});

const RAG = {
  kind: "rag" as const,
  ref: "rag",
  name: "문서 검색",
  description: "이 배포의 문서를 검색합니다.",
  risk_level: "read" as const,
  collections: [
    { id: "c1", name: "비료" },
    { id: "c2", name: "농약" },
  ],
};

test("the one rag entry becomes one row per collection", () => {
  const rows = mentionEntries([RAG], [], []);
  assert.deepEqual(
    rows.map((r) => [r.name, r.collectionId, r.ref]),
    [
      ["비료", "c1", "rag"],
      ["농약", "c2", "rag"],
    ],
  );
});

test("a deployment with no collections still offers the unscoped search", () => {
  const rows = mentionEntries([{ ...RAG, collections: [] }], [], []);
  assert.deepEqual(rows.map((r) => [r.name, r.collectionId]), [["문서 검색", undefined]]);
});

test("a row whose id this client does not hold is dropped, not shown", () => {
  const callables = [
    {
      kind: "workflow" as const,
      ref: "workflow:특허 조사",
      name: "특허 조사",
      description: null,
      risk_level: "read" as const,
      collections: [],
    },
    {
      kind: "mcp" as const,
      ref: "mcp:현장관측/water_quality",
      name: "현장관측/water_quality",
      description: null,
      risk_level: "write" as const,
      collections: [],
    },
  ];
  assert.deepEqual(mentionEntries(callables, [], []), []);

  const rows = mentionEntries(
    callables,
    [{ id: "w1", name: "특허 조사", description: null, answer_model: null, node_count: 4 }],
    [
      {
        id: "t1",
        server_name: "현장관측",
        name: "water_quality",
        description: null,
        risk_level: "write" as const,
        input_schema: {},
      },
    ],
  );
  assert.deepEqual(
    rows.map((r) => [r.kind, r.workflowId ?? r.toolId]),
    [
      ["workflow", "w1"],
      ["mcp", "t1"],
    ],
  );
});

test("the query filters by name, not by description", () => {
  const rows = mentionEntries([RAG], [], []);
  assert.deepEqual(filterEntries(rows, "농").map((r) => r.name), ["농약"]);
  // The description of every rag row contains 근거; no row matches on it.
  assert.deepEqual(filterEntries(rows, "근거"), []);
  assert.equal(filterEntries(rows, "  ").length, 2);
});
