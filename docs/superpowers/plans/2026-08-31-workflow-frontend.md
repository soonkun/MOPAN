# MOPAN — 워크플로우 프런트엔드 (Slice 6 front end) — Implementation Plan

> **Spec:** `docs/superpowers/specs/2026-08-31-slice-6-workflow-design.md`, sections 2, 3 and 6.
> **Backend plan:** `docs/superpowers/plans/2026-08-31-workflow-engine.md`, which built the API this
> one consumes and deliberately touched no file under `frontend/`. **Scope: `frontend/` and
> `docs/`.** No backend file is modified by this plan; `pytest` was therefore not run.

## The situation this plan starts from

The workflow backend landed complete. The front end had not been started, and `/agents` — a live,
linked screen — was calling `/api/agents*`, which the rename had deleted. The owner opened it on a
phone and got `요청을 처리하지 못했습니다. (HTTP 404)` above a form.

So the work is ordered by damage, not by interest:

1. **Unbreak `/agents`.** Point the screen at `/api/workflows*`, drop `orchestrator`, move the route
   to `/workflows` and leave a redirect behind. Shippable on its own.
2. **`@` in the composer.** One list of everything callable, filtered as you type, inserted as a
   chip - and it must not open while a Hangul syllable is being composed.
3. **The graph editor.** `/workflows` becomes a real canvas whose edges drive execution.

## Decisions

**The route moved and the old one redirects.** `/agents` is a 307 to `/workflows` from
`next.config.js`, not a second page rendering the same component: there is one screen at one
address, and the old address says so. Not 308 — a permanent redirect is cached by a browser
more or less forever, and it would make `/agents` unreachable if it ever means something again.

**`orchestrator` is gone from the row, so it is gone from the screen.** A saved PROCEDURE that
switched on autonomous PLANNING is the layering mistake the slice exists to remove. 슈퍼 에이전트 is
now only the per-conversation toggle in the composer, and the composer's "forced on, disabled" chip
went with the column.

**The `@` menu is not a `<dialog>`.** Every other overlay in that composer is one. This is the
exception because the whole gesture is that the user keeps typing: the textarea keeps focus and
becomes a `combobox` whose popup is the list, so the arrows and Enter are forwarded to it and the
phone keyboard never drops. A `showModal()` would take focus on the first `@`.

**The IME rule reuses the Enter guard's signal and does not invent a second one.** `isComposing`,
`keyCode === 229` and a ref set from the composition events, behind one `composingNow(native)`. The
menu never OPENS mid-composition — a list that opened there would take the next arrow or Enter for
itself, which is the keystroke the IME needed — and `compositionend` re-reads the token, which is
what makes `@농약` open on the syllable it commits rather than never.

**No graph library.** react-flow is ~50KB gzipped against five runtime dependencies, and what it
sells is pan/zoom, minimaps and drag-to-connect. The job here is: place a box, join two boxes, and
do both from a keyboard at 390px. That is absolutely positioned cards over one `<svg>`.

**Drag is never the route.** Nodes are `<button>`s (Enter opens settings, arrows move, Delete
removes) and an edge is made from two `<select>`s and a button. Port-dragging was rejected outright
rather than added-and-supplemented: it cannot be done without a pointer at all.

**The server's refusal is placed, not banner-ed.** `placeGraphError` decides which node or which
edge a Korean 400 is about; the text is the server's, verbatim. The cycle is the one case where the
client knows more than the server does — `그래프의 간선이 순환합니다.` carries no name because a
topological sort cannot say which edge is guilty, so a depth-first walk finds the back edge.

**Two files of logic live in `lib/`, not in the components.** `lib/mention.ts` and `lib/graph.ts`
hold the parts with rules in them, because `node --test --experimental-strip-types` can import a
`.ts` file and cannot import a component. That is what `lib/mention.test.ts` and `lib/graph.test.ts`
are for; the components next door are markup.

**Nothing is offered that the server refuses.** Every condition kind in the editor was saved against
the running backend and answered 201; `kind: "llm"` is NOT in the list because it answers 400, and
the dialog says so in Korean. The version-note field renders only when editing a saved workflow,
because `POST /api/workflows` takes no note and the field would otherwise save nowhere.

## The API this plan consumes

| method | path | used by |
|---|---|---|
| `GET` | `/api/workflows` | the list on `/workflows` |
| `POST` | `/api/workflows` | 만들기 — the graph rides the create |
| `PATCH` | `/api/workflows/{id}` | 저장, for the settings half |
| `DELETE` | `/api/workflows/{id}` | 삭제 |
| `GET` | `/api/workflows/{id}/versions` | the 되돌리기 list |
| `POST` | `/api/workflows/{id}/versions` | 저장, for the graph half |
| `POST` | `/api/workflows/{id}/versions/{v}/activate` | 되돌리기 |
| `GET` | `/api/workflows/selectable` | the composer's picker and `@` |
| `GET` | `/api/tools` | `@`, and the node dialog's 부를 것 |

---

### Task 1: unbreak the live screen

**Goal.** `/agents` stops 404-ing. The screen talks to `/api/workflows*`, `orchestrator` is gone
from every type and every control, and the route is `/workflows` with `/agents` redirecting.

**Why first.** It is the only part of this brief a user is currently hitting.

- [ ] **Step 1: Modify `frontend/lib/types.ts` — the workflow row replaces the agent row**

```typescript
/** One node of a workflow graph, exactly as `backend/app/workflow/graph.py`
 * writes it back out (`WorkflowGraph.to_raw`).
 *
 * `x`/`y` ride ALONG on the node rather than in a parallel layout blob, because
 * the backend stores them and a person arranged them: reopening the canvas has
 * to show the same picture. They are the one part the executor reads nothing
 * from, which is exactly why they belong here and cannot drift out of step.
 *
 * `tool` is the flat namespace `GET /api/tools` publishes: `rag`,
 * `mcp:서버/도구`, `workflow:이름`. `collections` is RAG only, and EMPTY MEANS
 * THE WHOLE ALLOWED CATALOGUE - which the canvas says out loud rather than
 * drawing as an empty list. */
export interface GraphNode {
  id: string;
  kind: "input" | "tool" | "branch" | "answer";
  label?: string;
  x: number;
  y: number;
  tool?: string;
  collections?: string[];
  arguments?: Record<string, unknown>;
  condition?: GraphCondition | null;
}

/** A branch condition. JSON, not a string grammar - see
 * `backend/app/workflow/expr.py`, which parses the reference by hand and has no
 * `eval` in it. `llm` is in the schema and is REFUSED at save. */
export interface GraphCondition {
  kind: "compare" | "exists" | "empty" | "and" | "or" | "not" | "llm";
  left?: unknown;
  op?: "==" | "!=" | ">" | ">=" | "<" | "<=";
  right?: unknown;
  of?: unknown;
}

/** An edge ORDERS execution and carries data - the thing `PlanStep.depends_on`
 * deliberately did not do. `when` is set only on an edge leaving a `branch`, and
 * the backend refuses a branch edge that has none. */
export interface GraphEdge {
  from: string;
  to: string;
  when?: "true" | "false";
}

export interface WorkflowGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** GET /api/workflows - the admin screen's row. Admin only, because the two
 * lists ARE the boundary and enumerating a boundary tells somebody what to try.
 *
 * `graph` is the ACTIVE version's, carried on the row so opening the canvas is
 * one request: splitting it into a second endpoint would guarantee a screen
 * showing one workflow's boxes over another's name at least once. */
export interface Workflow {
  id: string;
  name: string;
  description: string | null;
  /** A name from the prompt store, never the text: the store owns versioning
   * and attribution, and a workflow carrying its own copy would fork it out. */
  prompt_name: string;
  /** Null means the deployment's own ANSWER_MODEL. */
  answer_model: string | null;
  enabled: boolean;
  /** EMPTY MEANS UNRESTRICTED, for both lists. The screen prints 전체 허용
   * rather than 없음 beside an empty selection - that is the one place this
   * rule could mislead an admin, so it is the one place it is spelled out. */
  collections: { id: string; name: string }[];
  tools: { id: string; server_name: string; name: string; risk_level: McpRiskLevel }[];
  /** Null when no version is active, which makes a workflow uncallable rather
   * than broken. */
  active_version: number | null;
  graph: WorkflowGraph | null;
  created_by_email: string | null;
  created_at: string;
  updated_at: string;
}

/** GET /api/workflows/{id}/versions, newest first - the 되돌리기 list. Every
 * save is a version, and rolling back ACTIVATES an existing one rather than
 * copying it forward, so the history stays a history rather than growing a
 * duplicate every rollback. */
export interface WorkflowVersion {
  id: string;
  version: number;
  is_active: boolean;
  graph: WorkflowGraph;
  note: string | null;
  created_by_email: string | null;
  created_at: string;
}

/** GET /api/tools - ONE list, because RAG, MCP and workflows are one Tool
 * interface. It is what `@` opens in the composer and what the canvas offers on
 * a node.
 *
 * `ref` is what a graph node writes in its `tool` field and what a chip carries,
 * verbatim. `collections` is populated for the `rag` entry only: they are this
 * deployment's collections, so the composer can offer one search per collection
 * and the canvas can scope a search node. */
export interface CallableTool {
  kind: "rag" | "mcp" | "workflow";
  ref: string;
  name: string;
  description: string | null;
  risk_level: McpRiskLevel;
  collections: { id: string; name: string }[];
}

/** GET /api/workflows/selectable - what the composer's `@` menu and its picker
 * list. ENABLED workflows that have a graph, readable by any authenticated user,
 * and deliberately carrying neither boundary list: the boundary is not an
 * inventory to publish. */
export interface WorkflowOption {
  id: string;
  name: string;
  description: string | null;
  answer_model: string | null;
  /** How many nodes are in the graph it would run - the one number that says
   * this is a procedure rather than a prompt swap, without naming what it
   * reaches. */
  node_count: number;
}
```

- [ ] **Step 2: Modify `frontend/next.config.js` — the redirect that saves a bookmarked link**

```javascript
  // /agents was a real, linked, bookmarked screen until this slice renamed the
  // concept. The route is gone; a 404 for somebody's saved link is not the
  // apology the rename owes them. A REDIRECT rather than a second page that
  // renders the same thing: there is one screen, at one address, and the old
  // address says so.
  //
  // Not permanent (308). A 301/308 is cached by a browser more or less forever,
  // so if /agents ever means something again it would be unreachable from every
  // machine that had followed this once.
  async redirects() {
    return [{ source: "/agents", destination: "/workflows", permanent: false }];
  },
```

- [ ] **Step 3: Move `frontend/app/(app)/agents/page.tsx` to `frontend/app/(app)/workflows/page.tsx`** — `git mv`, then rewritten in Task 3. The list, the settings form and the delete confirmation are the same screen; what changes here is the API path, the type and the copy.

- [ ] **Step 4: Move `frontend/components/agents/AgentCanvas.tsx` to `frontend/components/workflows/WorkflowCanvas.tsx`** — and take the 슈퍼 에이전트 module, the 실행 방식 lane and the `orchestrator` field out of it. What is left is the BOUNDARY canvas: the collections, tools, prompt and model, none of which is a sequence.

- [ ] **Step 5: Write `frontend/components/chat/WorkflowPicker.tsx`** — `git mv` from `AgentPicker.tsx`, then rewritten. The picker stays beside `@`: a text gesture cannot be the only way to reach a setting, not on a phone keyboard and not for someone driving the app from the keyboard alone.

```tsx
"use client";

import PopoverSheet from "@/components/chat/PopoverSheet";
import type { WorkflowOption } from "@/lib/types";

/** Which WORKFLOW answers the next question - a saved procedure, with the
 * prompt, corpus scope, tool list and model it carries.
 *
 * It is the second route to the same choice `@` makes in the composer, and it
 * is deliberately kept: `@` is a text gesture, and a text gesture cannot be the
 * only way to reach a setting - not on a phone keyboard, and not for anyone
 * driving this from the keyboard alone.
 *
 * The same controlled PopoverSheet ModelPicker.tsx is, and deliberately still
 * not a shared abstraction of its LIST: the two differ in what a row carries
 * (this one has a null 기본 row and a description line), and an
 * options-and-slots component covering both would be longer than the second
 * copy. What they did share - the anchored dialog, Escape, focus return - is
 * PopoverSheet, and that is now written once.
 *
 * The one behaviour worth reading twice is the same one ModelPicker documents
 * at length: `change` commits the choice so arrow keys can browse, and `click`
 * with `detail > 0` - a real pointer press - is what closes. */

// No workflow has no row in the database, so it has no id. `null` is that state
// everywhere in this client, and it is what makes "no workflows configured" and
// "the 기본 row is selected" the same state rather than two.
export const DEFAULT_WORKFLOW_LABEL = "기본";

export default function WorkflowPicker({
  workflows,
  value,
  onChange,
  open,
  onClose,
  anchorRef,
}: {
  workflows: WorkflowOption[];
  value: string | null;
  onChange: (id: string | null) => void;
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement | null>;
}) {
  const rows: (WorkflowOption | null)[] = [null, ...workflows];

  return (
    <PopoverSheet open={open} onClose={onClose} anchorRef={anchorRef} label="워크플로우">
      <fieldset className="border-0 p-2 pb-6 sm:pb-2">
        <legend className="px-3 py-2 text-label font-medium text-on-surface-variant">
          워크플로우
        </legend>
        {rows.map((workflow) => (
          <label
            key={workflow?.id ?? "default"}
            className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-3 transition-colors duration-150 hover:bg-surface-container-high has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-primary sm:py-2"
          >
            <input
              type="radio"
              name="chat-workflow"
              value={workflow?.id ?? ""}
              checked={(workflow?.id ?? null) === value}
              onChange={() => onChange(workflow?.id ?? null)}
              onClick={(event) => {
                if (event.detail > 0) onClose();
              }}
              onKeyDown={(event) => {
                if (event.key === " " || event.key === "Enter") onClose();
              }}
              className="sr-only"
            />
            <span aria-hidden="true" className="h-4 w-4 shrink-0 text-primary">
              {(workflow?.id ?? null) === value && (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="m5 13 4 4L19 7" />
                </svg>
              )}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-body">
                {workflow?.name ?? DEFAULT_WORKFLOW_LABEL}
              </span>
              <span className="block truncate text-caption text-on-surface-variant">
                {workflow
                  ? `${workflow.description || "설명 없음"} · 노드 ${workflow.node_count}개`
                  : "이 배포의 기본 설정으로 답변합니다."}
              </span>
            </span>
          </label>
        ))}
      </fieldset>
    </PopoverSheet>
  );
}
```

- [ ] **Step 6: Modify `frontend/components/chat/ChatWindow.tsx` — `agentId` becomes `workflowId`**

```tsx
  /** Picking a workflow also moves the MODEL picker to the workflow's model.
   *
   * The server treats the workflow's model as a default an explicit `model`
   * still overrides, so leaving the picker where it was would send the old model
   * and silently ignore the workflow's - the user would have configured a model
   * on it and never seen it used. Moving the visible control is what makes the
   * two agree, and the user can still change it afterwards. */
  function chooseWorkflow(id: string | null) {
    setWorkflowId(id);
    const workflow = workflows.find((w) => w.id === id) ?? null;
    setNotice(
      workflow ? `${workflow.name} 워크플로우로 답변합니다.` : "워크플로우 없이 답변합니다.",
    );
    if (workflow?.answer_model && models.some((m) => m.id === workflow.answer_model)) {
      setModel(workflow.answer_model);
    }
    try {
      if (id) localStorage.setItem(WORKFLOW_STORAGE_KEY, id);
      else localStorage.removeItem(WORKFLOW_STORAGE_KEY);
    } catch {
      // The choice still applies to this session; it just will not survive a
      // reload. Nothing to tell the user about.
    }
  }

  /** The 문서 검색 rows of the `@` menu. Announced like every other choice made
   * in the composer, because the chip is small and the consequence is not:
   * scoping to one collection is the difference between "the corpus does not
   * say" and "this part of it does not". */
  function chooseCollection(id: string | null) {
    setCollectionId(id);
    const name = callables
      .find((c) => c.kind === "rag")
      ?.collections.find((c) => c.id === id)?.name;
    setNotice(name ? `${name} 분류에서만 찾습니다.` : "분류 제한을 풀었습니다.");
  }
```

- [ ] **Step 7: Modify `frontend/components/layout/Sidebar.tsx`** — the nav label becomes 워크플로우 at `/workflows`.

```tsx
    { href: "/workflows", label: "워크플로우" },
    { href: "/settings", label: "고급 설정" },
```

- [ ] **Step 8: Modify `frontend/app/(app)/prompts/page.tsx`** — it pointed at 에이전트 생성, a screen that no longer exists.

```tsx
          워크플로우마다 다른 답변 지침을 쓰려면 여기에서 새 프롬프트를 만든 뒤, 워크플로우
          화면에서 선택하세요. 기존 이름은 덮어쓰지 않습니다.
```

---

### Task 2: `@` in the composer

**Goal.** Type `@`, get one list of everything callable, filter it by typing, pick with the arrows
and Enter, and get a chip. It must not open during a Hangul composition.

**Why the logic is in `lib/`.** Where a token starts and ends, which rows exist and what the query
matches are the parts that can be wrong invisibly. They are also the parts a test can reach without
a DOM.

- [ ] **Step 1: Write `frontend/lib/mention.ts`**

```typescript
import type { CallableTool, McpToolOption, WorkflowOption } from "@/lib/types";

/** What `@` in the composer is made of, with no JSX in it.
 *
 * Here rather than in MentionMenu.tsx for one reason: this is the part with
 * rules in it - where a token starts, which rows exist, what a query matches -
 * and `node --test --experimental-strip-types` can import a .ts file and cannot
 * import a component. The menu next door is markup; this is the logic, and
 * lib/mention.test.ts is what holds it to it.
 */

/** One row of the menu. The three kinds are flattened into one list
 * deliberately - a menu grouped by transport would be asking the user to know
 * what MCP is. */
export type MentionEntry = {
  /** Unique per row, for React and for the `aria-activedescendant` id. */
  key: string;
  kind: "rag" | "mcp" | "workflow";
  /** What the filter matches and what the row shows. */
  name: string;
  description: string | null;
  riskLevel: string;
  /** The namespace this row names, verbatim, exactly as a graph node writes it.
   * Shown on the row so the thing a person picks in chat and the thing they drop
   * on the canvas are visibly the same thing. */
  ref: string;
  /** RAG rows only: which collection this row would scope the search to. */
  collectionId?: string;
  /** Workflow rows only: the id POST /api/chat takes. */
  workflowId?: string;
  /** MCP rows only: the id POST /api/chat takes. */
  toolId?: string;
};

/** The `@…` token the caret is sitting in, or null.
 *
 * The `@` has to follow the start of the text or whitespace, so an email address
 * typed into a question never opens the menu. The token ends AT THE CARET, so
 * moving back into a sentence that already contains an `@` does not reopen it,
 * and it cannot contain whitespace or a second `@`.
 *
 * `start` is where the `@` itself is, which is what a pick removes from.
 */
export function mentionAt(
  value: string,
  caret: number | null,
): { start: number; query: string } | null {
  if (caret === null) return null;
  const match = /(^|\s)@([^\s@]*)$/.exec(value.slice(0, caret));
  if (!match) return null;
  return { start: caret - match[2].length - 1, query: match[2] };
}

/** `GET /api/tools` into rows this composer can actually act on.
 *
 * The `rag` entry arrives as ONE row carrying the deployment's collections, and
 * is expanded here into one row per collection: "부를 수 있는 것" for retrieval is
 * a corpus, not the search function. A deployment with no collections still gets
 * the single unscoped row, because searching everything is a real thing to ask
 * for.
 *
 * A workflow or an MCP row whose id this client does not hold is DROPPED rather
 * than shown: `/api/tools` and the two id-carrying lists are fetched separately,
 * so a row can exist in one and not yet the other, and a row that cannot be
 * acted on is worse than a row that is not there. Those ids are what
 * POST /api/chat takes.
 */
export function mentionEntries(
  callables: CallableTool[],
  workflows: WorkflowOption[],
  tools: McpToolOption[],
): MentionEntry[] {
  const entries: MentionEntry[] = [];
  for (const callable of callables) {
    if (callable.kind === "rag") {
      if (callable.collections.length === 0) {
        entries.push({
          key: "rag",
          kind: "rag",
          name: callable.name,
          description: callable.description,
          riskLevel: callable.risk_level,
          ref: callable.ref,
        });
        continue;
      }
      for (const collection of callable.collections) {
        entries.push({
          key: `rag:${collection.id}`,
          kind: "rag",
          name: collection.name,
          description: "이 분류 안에서만 근거를 찾습니다.",
          riskLevel: callable.risk_level,
          ref: callable.ref,
          collectionId: collection.id,
        });
      }
    } else if (callable.kind === "mcp") {
      const tool = tools.find((t) => `mcp:${t.server_name}/${t.name}` === callable.ref);
      if (!tool) continue;
      entries.push({
        key: callable.ref,
        kind: "mcp",
        name: callable.name,
        description: callable.description,
        riskLevel: callable.risk_level,
        ref: callable.ref,
        toolId: tool.id,
      });
    } else {
      const workflow = workflows.find((w) => `workflow:${w.name}` === callable.ref);
      if (!workflow) continue;
      entries.push({
        key: callable.ref,
        kind: "workflow",
        name: callable.name,
        description: callable.description,
        riskLevel: callable.risk_level,
        ref: callable.ref,
        workflowId: workflow.id,
      });
    }
  }
  return entries;
}

/** Case-insensitive substring on the NAME, which is what the user typed after
 * the `@`. Not on the description: a two-character query would then match half
 * the list through prose nobody was reading. */
export function filterEntries(entries: MentionEntry[], query: string): MentionEntry[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return entries;
  return entries.filter((entry) => entry.name.toLowerCase().includes(needle));
}
```

- [ ] **Step 2: Write `frontend/lib/mention.test.ts`**

```typescript
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
```

- [ ] **Step 3: Write `frontend/components/chat/MentionMenu.tsx`**

```tsx
"use client";

import type { MentionEntry } from "@/lib/mention";

/** The list `@` opens in the composer.
 *
 * ONE list, because there is one Tool interface: `GET /api/tools` returns the
 * document search, every enabled MCP tool and every callable workflow in a
 * single namespace, and `ref` - `rag`, `mcp:서버/도구`, `workflow:이름` - is what
 * a graph node and a chip both write verbatim.
 *
 * NOT a <dialog>. Every other overlay in this composer is one, and this is the
 * exception on purpose: a modal takes focus, and the whole gesture here is that
 * the user keeps typing. The textarea stays focused and stays the thing being
 * driven - it becomes a combobox, the list is its popup, and the arrows and
 * Enter are forwarded to it. That is also what keeps the phone keyboard up: a
 * showModal() would drop it on the first `@`.
 *
 * The IME rule lives in Composer, not here, because it is a property of the
 * KEYSTROKE rather than of the list: see `updateMention` there. What a row IS,
 * and what the query matches, is in lib/mention.ts, where a test can reach it.
 */

const KIND_LABEL: Record<MentionEntry["kind"], string> = {
  rag: "문서 검색",
  mcp: "MCP 도구",
  workflow: "워크플로우",
};

const RISK_LABEL: Record<string, string> = {
  read: "읽기",
  write: "쓰기",
  destructive: "파괴적",
};

export default function MentionMenu({
  id,
  entries,
  activeIndex,
  query,
  onPick,
  onHover,
}: {
  id: string;
  entries: MentionEntry[];
  activeIndex: number;
  query: string;
  onPick: (entry: MentionEntry) => void;
  onHover: (index: number) => void;
}) {
  return (
    <div className="p-1 pb-2">
      {/* max-h in vh, not px: at 390px with the keyboard up the visual viewport
          is about 340px tall, and a fixed 320px list would cover the composer
          that is producing it. */}
      <ul
        id={id}
        role="listbox"
        aria-label="부를 수 있는 것"
        className="max-h-[40vh] overflow-y-auto rounded-md bg-surface-container-high p-1 shadow-menu"
      >
        {entries.length === 0 ? (
          <li className="px-3 py-3 text-body text-on-surface-variant">
            {query.trim() ? `"${query.trim()}"에 맞는 것이 없습니다.` : "부를 수 있는 것이 없습니다."}
          </li>
        ) : (
          entries.map((entry, index) => (
            <li
              key={entry.key}
              id={`${id}-${index}`}
              role="option"
              aria-selected={index === activeIndex}
              // onMouseDown, not onClick, and preventDefault with it: a click
              // moves focus out of the textarea first, which on a phone drops
              // the keyboard and on a desktop closes the menu before the pick
              // has been read.
              onMouseDown={(event) => {
                event.preventDefault();
                onPick(entry);
              }}
              onMouseEnter={() => onHover(index)}
              className={`cursor-pointer rounded-sm px-3 py-2 transition-colors duration-150 ${
                index === activeIndex
                  ? "bg-primary-container text-on-primary-container"
                  : "text-on-surface"
              }`}
            >
              <span className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 truncate text-body font-medium">{entry.name}</span>
                <span
                  className={`shrink-0 text-caption ${
                    index === activeIndex ? "text-on-primary-container" : "text-on-surface-variant"
                  }`}
                >
                  {KIND_LABEL[entry.kind]}
                  {entry.riskLevel !== "read" && ` · ${RISK_LABEL[entry.riskLevel] ?? entry.riskLevel}`}
                </span>
              </span>
              <span
                className={`block truncate text-caption ${
                  index === activeIndex ? "text-on-primary-container" : "text-on-surface-variant"
                }`}
              >
                {entry.description || entry.ref}
              </span>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}
```

- [ ] **Step 4: Modify `frontend/components/chat/Composer.tsx` — the one IME signal, shared with the Enter guard**

```tsx
  /** THE ONE IME SIGNAL, used by Enter and by `@` alike.
   *
   * There is deliberately no second mechanism for the menu. `isComposing` is the
   * standard, `keyCode === 229` is what the engines that predate it report, and
   * the ref covers an engine that fires compositionend late; a fourth check
   * invented for the menu would be a fourth thing to get wrong, and the two
   * behaviours would drift apart on exactly one browser.
   *
   * An InputEvent carries `isComposing` and no `keyCode`; a KeyboardEvent
   * carries both; a CompositionEvent carries neither, which is why
   * compositionend can re-evaluate the token and open the menu on the syllable
   * it just committed. */
  function composingNow(native: Event): boolean {
    const event = native as InputEvent & KeyboardEvent;
    return composingRef.current || event.isComposing === true || event.keyCode === 229;
  }
```

- [ ] **Step 5: Modify `frontend/components/chat/Composer.tsx` — opening, filtering and picking**

```tsx
  /** Re-read the token under the caret after anything that could have changed it.
   *
   * **The menu never OPENS mid-composition.** While a Hangul syllable is still
   * being composed the `@` in front of it is not committed, and a list that
   * opened there would take the next arrow or Enter for itself - the keystroke
   * the IME needed. An ALREADY-open menu keeps following the composing text,
   * because that is only the filter narrowing and steals nothing: Enter is still
   * refused by the same guard. compositionend calls this again, so `@농약` opens
   * on the syllable it commits rather than never. */
  function updateMention(value: string, caret: number | null, native: Event) {
    const next = mentionAt(value, caret);
    if (next === null) {
      setMention(null);
      return;
    }
    if (mention === null && composingNow(native)) return;
    setMention(next);
    if (next.query !== mention?.query) setActiveIndex(0);
  }

  /** A row was chosen. The `@…` text goes; the CHIP is what carries the choice
   * from here, and leaving the token behind would send it to the model as part
   * of the question. */
  function pick(entry: MentionEntry) {
    if (!mention) return;
    const at = mention;
    onChange(value.slice(0, at.start) + value.slice(at.start + 1 + at.query.length));
    setMention(null);
    if (entry.kind === "workflow" && entry.workflowId) {
      onWorkflowChange(entry.workflowId);
    } else if (entry.kind === "rag") {
      onCollectionChange(entry.collectionId ?? null);
    } else if (entry.kind === "mcp" && entry.toolId) {
      // The arguments still have to be filled in, and the tool's own
      // input_schema is the only thing that knows what they are - so this hands
      // off to the picker that renders it, opened on the row just chosen.
      setToolSeed(entry.toolId);
      setSheet("tool");
    }
    // The caret back where the token was, in a frame that runs after React has
    // written the shortened value - setSelectionRange against the old text would
    // land in the wrong place.
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      el?.focus();
      el?.setSelectionRange(at.start, at.start);
    });
  }
```

- [ ] **Step 6: Modify `frontend/components/chat/Composer.tsx` — the textarea becomes a combobox**

```tsx
          onKeyDown={(e) => {
            // A Korean user pressing Enter to CONFIRM a Hangul candidate must
            // not send the message, and must not pick a row out of the `@` menu
            // either - that Enter belongs to the IME. Three checks because no
            // single one is portable: `isComposing` is the standard, keyCode 229
            // is what the engines that predate it report, and the ref covers an
            // engine that fires compositionend late.
            const composing = composingNow(e.nativeEvent);
            // The menu owns the arrows, Enter, Tab and Escape while it is open -
            // and only while it is open, so nothing about typing changes when it
            // is not. This is what makes `@` a keyboard gesture end to end: no
            // pointer touches the list at any point.
            if (mention && !composing) {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                if (visible.length === 0) return;
                const step = e.key === "ArrowDown" ? 1 : visible.length - 1;
                setActiveIndex((index) => (Math.min(index, visible.length - 1) + step) % visible.length);
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                setMention(null);
                return;
              }
              if ((e.key === "Enter" || e.key === "Tab") && active) {
                e.preventDefault();
                pick(active);
                return;
              }
            }
            if (e.key !== "Enter" || e.shiftKey) return;
            if (composing) {
              return;
            }
            // Shift+Enter is handled by the early return above: it falls
            // through to the textarea's own newline insertion.
            e.preventDefault();
            onSubmit();
          }}
```

- [ ] **Step 7: Modify `frontend/components/chat/ToolPicker.tsx`** — `@` naming an MCP tool hands off to the picker that renders its `input_schema`, opened on the row just chosen.

```tsx
  /** Which tool to open ON. Set when `@` picked one by name: the row the user
   * chose is already the answer to "which tool", and re-asking it in the dialog
   * would make the `@` gesture a slower route to the same list. Null - the +
   * menu's 도구 사용 row - leaves the previous selection alone, which is what
   * lets somebody adjust the arguments of the tool they just used. */
  initialToolId?: string | null;
  /** A dismissal, a 취소 or a committed 추가. Focus return belongs to the
   * composer's `closeSheet`, which is the one owner of it - see PopoverSheet. */
  onClose: () => void;
```

- [ ] **Step 8: Modify `frontend/components/chat/ChatWindow.tsx`** — `GET /api/tools` and the collection scope a 문서 검색 row sets.

```tsx
  /** The 문서 검색 rows of the `@` menu. Announced like every other choice made
   * in the composer, because the chip is small and the consequence is not:
   * scoping to one collection is the difference between "the corpus does not
   * say" and "this part of it does not". */
  function chooseCollection(id: string | null) {
    setCollectionId(id);
    const name = callables
      .find((c) => c.kind === "rag")
      ?.collections.find((c) => c.id === id)?.name;
    setNotice(name ? `${name} 분류에서만 찾습니다.` : "분류 제한을 풀었습니다.");
  }
```

---

### Task 3: the graph editor

**Goal.** `/workflows` draws the graph, edits it from the keyboard, saves a version, rolls one back,
and puts the server's refusal on the node or the edge it is about.

**The one thing to get right.** The edges now drive execution, so the picture is a claim about
behaviour. Everything else follows from that: coordinates are stored because a person arranged them,
`input`/`answer` cannot be deleted because a graph without them saves and will not run, and the
canvas's old paragraph about deliberately having no seat for drawing an order is DELETED rather than
softened.

- [ ] **Step 1: Write `frontend/lib/graph.ts`**

```typescript
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
```

- [ ] **Step 2: Write `frontend/lib/graph.test.ts`**

```typescript
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
```

- [ ] **Step 3: Write `frontend/components/workflows/GraphEditor.tsx`**

```tsx
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
```

- [ ] **Step 4: Write `frontend/app/(app)/workflows/page.tsx`**

```tsx
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
```

- [ ] **Step 5: Modify `frontend/components/workflows/WorkflowCanvas.tsx`** — delete the paragraph that says there is deliberately no seat for drawing an order. It was true when the executor read no edges; it became false the moment one did.

```tsx
      {/* Where the order lives now, said once, because this canvas used to
          claim there was nowhere to draw one. */}
      <p className="rounded-md bg-surface-container px-4 py-3 text-caption text-on-surface-variant">
        실행 순서는 위의 그래프가 정합니다. 여기 놓인 모듈에는 순서가 없습니다 — 이 워크플로우가
        무엇에 닿을 수 있는지, 어떤 지침과 모델로 답하는지만 정합니다.
      </p>
```

- [ ] **Step 6: Modify `frontend/components/chat/TraceDialog.tsx`** — one trace shape whoever authored the graph, and a node line that does not tell the 답변 node it searched the corpus.

```tsx
/** The node kinds, for the line under a step. `rag` is not here: it is the
 * pre-Slice-6 kind for a search step, and it is rendered as one. */
const NODE_KIND_LABEL: Record<string, string> = {
  input: "질문이 들어온 자리",
  branch: "분기",
  answer: "모인 근거로 답변",
};

const PLAN_STATE_LABEL: Record<PlanStep["state"], string> = {
  running: "진행 중",
  done: "완료",
  failed: "실패",
  skipped: "건너뜀",
  timeout: "시간 초과",
};
```

- [ ] **Step 7: Modify `frontend/package.json` — the two new test files**

```json
    "test": "node --test --experimental-strip-types --disable-warning=ExperimentalWarning --disable-warning=MODULE_TYPELESS_PACKAGE_JSON lib/api.test.ts lib/mention.test.ts lib/graph.test.ts"
```

- [ ] **Step 8: Modify `scripts/check_all_plans.py`**

```python
PLANS = [
    # Slice 1's plan is frozen history: its files have all been superseded by
    # the plans below, and re-listing it would only re-open blocks that the
    # later work legitimately replaced.
    "docs/superpowers/plans/2026-08-30-management-screens.md",
    "docs/superpowers/plans/2026-08-30-model-selection.md",
    "docs/superpowers/plans/2026-08-30-prompt-admin.md",
    "docs/superpowers/plans/2026-08-30-slice-5-observability.md",
    "docs/superpowers/plans/2026-08-30-slice-2-mcp.md",
    "docs/superpowers/plans/2026-08-30-slice-3-orchestrator.md",
    "docs/superpowers/plans/2026-08-30-slice-4-agents.md",
    "docs/superpowers/plans/2026-08-30-neighbour-expansion.md",
    "docs/superpowers/plans/2026-08-30-prompt-budget.md",
    "docs/superpowers/plans/2026-08-31-ui-masthead-composer-sidebar.md",
    "docs/superpowers/plans/2026-08-31-agent-builder.md",
    # Slice 6. It supersedes the backend halves of the slice-3, slice-4 and
    # agent-builder plans: `app/orchestrator/` and `app/agents/` no longer exist,
    # and rule 3 reads a later plan's block for a path as replacing an earlier
    # one's. It says nothing about `frontend/`, which is another agent's.
    "docs/superpowers/plans/2026-08-31-workflow-engine.md",
    # Slice 6's front end. It supersedes the frontend halves of the slice-4 and
    # agent-builder plans - `components/agents/` and `app/(app)/agents/` no
    # longer exist - and says nothing about `backend/`, which the plan above
    # owns.
    "docs/superpowers/plans/2026-08-31-workflow-frontend.md",
]
```

- [ ] **Step 9: Modify `docs/화면.md`** — the new sections, and the correction to the one this slice falsified. The old 에이전트 생성 section stays as history with its claim struck through: *선을 잇는 자리는 일부러 두지 않았습니다* was true while nothing read the edges, and this slice is what made it false.

```markdown
## 워크플로우 — 간선이 실행 순서를 정합니다

<img src="screenshots/workflows-list.png" width="820" alt="워크플로우 화면 전체">

`에이전트 생성`이 있던 자리입니다. 이름만 바뀐 것이 아닙니다.

- 경로가 `/agents` → `/workflows`로 옮겨졌습니다. 예전 주소는 새 주소로 넘겨줍니다
  (307). 저장해 둔 링크가 404가 되지 않게 하려는 것이고, 영구 리다이렉트(308)로 하지
  않은 이유는 브라우저가 그것을 사실상 영원히 캐시하기 때문입니다.
- **`agents.orchestrator` 컬럼이 없어졌습니다.** 저장된 절차가 자율 계획을 켜고 있던
  것이 층위를 섞은 원인이었습니다. 슈퍼 에이전트는 이제 입력창의 대화 단위 토글
  **하나뿐**이고, 이 화면에는 그 스위치가 없습니다.
```

---

### Task 4: drive it, photograph it, and write down what is not built

**Goal.** Every claim on the screen checked against the running stack, and the ones that could not be
checked said out loud.

**What was driven** (Chromium, `http://localhost:3000`, admin account):

- `/agents` → 307 → `/workflows`, `h1` = 워크플로우, zero failing `/api/*` responses.
- `@` opens on one keystroke, filters to nothing on `@일zzz`, inserts a chip, and clears the token
  from the textarea.
- The IME rule, through the Chrome DevTools Protocol's own `Input.imeSetComposition`: the menu does
  not open during a composition, the composed syllable is not eaten when it is already open, and
  Enter mid-composition neither sends the question nor picks a row.
- A branching graph built on the canvas, saved (201), saved again (v2), rolled back to v1 — the node
  moved back — a cycle refused (400) with `그래프의 간선이 순환합니다.` rendered on the `n2 → search`
  row, then the cycle removed and saved again.
- The same workflow answering a real question from `@`, with the trace showing five nodes and
  `질문 그대로 다시 찾기` as 건너뜀 · 분기에서 선택되지 않았습니다.
- The whole editor from the keyboard alone: Tab to 새로 만들기, Enter, type a name, Tab to 분기 노드
  추가, Enter, arrows to move, Enter to open, Escape to close and return focus, two selects and a
  button to draw an edge, Delete to remove the node and its edges.
- 390px: no horizontal overflow on `/workflows` or on the composer with the menu open.
- Every condition kind the editor offers, saved against the running backend: 201 for compare
  (numeric and string), exists, empty, and, or, not. `kind: "llm"` answers
  `400 모델 판단 분기(kind: llm)는 아직 켜져 있지 않습니다.` and is therefore not offered.
- A `workflow:` node saved (201); a workflow calling itself refused with
  `워크플로우가 자기 자신을 다시 부릅니다: 호출하는 워크플로우`.

**Two defects found by driving, and fixed:**

- At 390px the graph's width grew the grid track and `main` scrolled the whole app sideways — the
  `h1` was clipped and the page read as broken. `min-w-0` on the form makes the canvas's own
  `overflow-x-auto` the thing that scrolls.
- Ending a pointer drag also fired the card's click, so every drag opened the settings dialog over
  the node just moved. The drag now remembers that it moved and swallows that one click.

**What is NOT built, and why** — recorded here because a limitation that lives only in a code
comment has not been communicated:

- **MCP 도구 노드는 실제 서버로 확인하지 못했습니다.** 이 배포에는 등록된 MCP 서버가 없어서
  `GET /api/tools`가 MCP 항목을 하나도 내려주지 않습니다. 노드 설정 창이 도구의 `input_schema`로
  인자 칸을 만드는 경로와, `@`에서 MCP 도구를 골라 ToolPicker로 넘기는 경로는 **코드로만 있고
  실행으로 확인되지 않았습니다.** 서버를 하나 등록한 뒤 다시 확인해야 합니다.
- **`items.N.*` 참조는 드롭다운에 없습니다.** `N`이 실행 시점에만 정해지는 숫자라 목록으로 만들 수
  없습니다. 직접 입력으로는 칠 수 있고 서버도 받습니다.
- **삭제된 분류를 고른 칩은 전송 시점에 거절되지 않습니다.** 워크플로우(404·409)와 MCP
  도구(404)는 서버가 거절하지만, `collection_ids`에 담긴 사라진 분류는 워크플로우 제한이 걸려 있지
  않은 한 그냥 아무 근거도 찾지 못하는 검색이 됩니다. 서버에 그 거절이 없기 때문이고, 프런트에서
  한국어 문장을 새로 지어내면 그것은 서버의 말이 아닙니다.
- **`/workflows`까지 Tab이 197번 걸립니다.** 사이드바의 대화 기록이 전부 탭 순서에 들어 있어서이고,
  이 화면만의 문제가 아니라 모든 관리 화면이 그렇습니다. 건너뛰기 링크는 레이아웃 변경이라
  이번 범위 밖으로 두었습니다.

- [ ] **Step 1: Run** `cd frontend && npx tsc --noEmit && npm test && npm run build`, then `docker compose build frontend` and `docker compose up -d --force-recreate --no-deps frontend` — the rebuild is not enough on this machine; the container has to be recreated, and the served bundle grepped for a string only the new build has.
