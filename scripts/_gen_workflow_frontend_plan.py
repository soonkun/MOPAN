#!/usr/bin/env python3
"""Emit docs/superpowers/plans/2026-08-31-workflow-frontend.md from disk.

Same device as `scripts/_gen_slice6_plan.py`, for the same reason: the plan is
the durable transcription source and `scripts/check_plan_parity.py` compares
every block in it against the file it names. Writing those blocks by hand is how
four parity claims in this project turned out to be false. So the PROSE is
authored here and the CODE is read off disk, which makes drift impossible by
construction rather than by care.

Run it again after any change to the files it names; it is idempotent.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs/superpowers/plans/2026-08-31-workflow-frontend.md"


def whole(path: str, lang: str = "typescript") -> str:
    return f"```{lang}\n{(REPO / path).read_text(encoding='utf-8').rstrip()}\n```"


def between(path: str, start: str, end: str, lang: str = "typescript") -> str:
    """A verbatim slice of a file, from the line containing `start` up to but not
    including the line containing `end`. Read off disk, so a `Modify` step's
    snippet cannot drift from what is actually there."""
    text = (REPO / path).read_text(encoding="utf-8")
    i = text.index(start)
    j = text.index(end, i)
    return f"```{lang}\n{text[i:j].rstrip()}\n```"


PARTS: list[str] = []


def add(text: str) -> None:
    PARTS.append(text.strip("\n"))


add(
    """
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
"""
)

# ---------------------------------------------------------------- Task 1
add(
    """
---

### Task 1: unbreak the live screen

**Goal.** `/agents` stops 404-ing. The screen talks to `/api/workflows*`, `orchestrator` is gone
from every type and every control, and the route is `/workflows` with `/agents` redirecting.

**Why first.** It is the only part of this brief a user is currently hitting.
"""
)
add("- [ ] **Step 1: Modify `frontend/lib/types.ts` — the workflow row replaces the agent row**")
add(
    between(
        "frontend/lib/types.ts",
        "/** One node of a workflow graph, exactly as `backend/app/workflow/graph.py`",
        "/** GET /api/models - the admin's ANSWER_MODELS allowlist",
    )
)
add(
    "- [ ] **Step 2: Modify `frontend/next.config.js` — the redirect that saves a bookmarked link**"
)
add(
    between(
        "frontend/next.config.js",
        "  // /agents was a real, linked, bookmarked screen",
        "\n  // See the note above rewrites()",
        lang="javascript",
    )
)
add(
    "- [ ] **Step 3: Move `frontend/app/(app)/agents/page.tsx` to "
    "`frontend/app/(app)/workflows/page.tsx`** — `git mv`, then rewritten in Task 3. The list, the "
    "settings form and the delete confirmation are the same screen; what changes here is the API "
    "path, the type and the copy."
)
add(
    "- [ ] **Step 4: Move `frontend/components/agents/AgentCanvas.tsx` to "
    "`frontend/components/workflows/WorkflowCanvas.tsx`** — and take the 슈퍼 에이전트 module, the "
    "실행 방식 lane and the `orchestrator` field out of it. What is left is the BOUNDARY canvas: the "
    "collections, tools, prompt and model, none of which is a sequence."
)
add(
    "- [ ] **Step 5: Write `frontend/components/chat/WorkflowPicker.tsx`** — `git mv` from "
    "`AgentPicker.tsx`, then rewritten. The picker stays beside `@`: a text gesture cannot be the "
    "only way to reach a setting, not on a phone keyboard and not for someone driving the app from "
    "the keyboard alone."
)
add(whole("frontend/components/chat/WorkflowPicker.tsx", lang="tsx"))
add(
    "- [ ] **Step 6: Modify `frontend/components/chat/ChatWindow.tsx` — `agentId` becomes "
    "`workflowId`**"
)
add(
    between(
        "frontend/components/chat/ChatWindow.tsx",
        "  /** Picking a workflow also moves the MODEL picker",
        "\n  function chooseModel(",
        lang="tsx",
    )
)
add(
    "- [ ] **Step 7: Modify `frontend/components/layout/Sidebar.tsx`** — the nav label becomes "
    "워크플로우 at `/workflows`."
)
add(
    between(
        "frontend/components/layout/Sidebar.tsx",
        '    { href: "/workflows", label: "워크플로우" }',
        "\n  ];",
        lang="tsx",
    )
)
add(
    "- [ ] **Step 8: Modify `frontend/app/(app)/prompts/page.tsx`** — it pointed at 에이전트 생성, "
    "a screen that no longer exists."
)
add(
    between(
        "frontend/app/(app)/prompts/page.tsx",
        "          워크플로우마다 다른 답변 지침을",
        "\n        </p>",
        lang="tsx",
    )
)

# ---------------------------------------------------------------- Task 2
add(
    """
---

### Task 2: `@` in the composer

**Goal.** Type `@`, get one list of everything callable, filter it by typing, pick with the arrows
and Enter, and get a chip. It must not open during a Hangul composition.

**Why the logic is in `lib/`.** Where a token starts and ends, which rows exist and what the query
matches are the parts that can be wrong invisibly. They are also the parts a test can reach without
a DOM.
"""
)
add("- [ ] **Step 1: Write `frontend/lib/mention.ts`**")
add(whole("frontend/lib/mention.ts"))
add("- [ ] **Step 2: Write `frontend/lib/mention.test.ts`**")
add(whole("frontend/lib/mention.test.ts"))
add("- [ ] **Step 3: Write `frontend/components/chat/MentionMenu.tsx`**")
add(whole("frontend/components/chat/MentionMenu.tsx", lang="tsx"))
add(
    "- [ ] **Step 4: Modify `frontend/components/chat/Composer.tsx` — the one IME signal, shared "
    "with the Enter guard**"
)
add(
    between(
        "frontend/components/chat/Composer.tsx",
        "  /** THE ONE IME SIGNAL, used by Enter and by `@` alike.",
        "\n  // Auto-grow, 1 to 8 rows.",
        lang="tsx",
    )
)
add(
    "- [ ] **Step 5: Modify `frontend/components/chat/Composer.tsx` — opening, filtering and "
    "picking**"
)
add(
    between(
        "frontend/components/chat/Composer.tsx",
        "  /** Re-read the token under the caret after anything that could have changed it.",
        "\n  /** The ONE owner of focus return",
        lang="tsx",
    )
)
add(
    "- [ ] **Step 6: Modify `frontend/components/chat/Composer.tsx` — the textarea becomes a "
    "combobox**"
)
add(
    between(
        "frontend/components/chat/Composer.tsx",
        "          onKeyDown={(e) => {",
        "\n          onPaste={(e) => {",
        lang="tsx",
    )
)
add(
    "- [ ] **Step 7: Modify `frontend/components/chat/ToolPicker.tsx`** — `@` naming an MCP tool "
    "hands off to the picker that renders its `input_schema`, opened on the row just chosen."
)
add(
    between(
        "frontend/components/chat/ToolPicker.tsx",
        "  /** Which tool to open ON.",
        "\n}) {",
        lang="tsx",
    )
)
add(
    "- [ ] **Step 8: Modify `frontend/components/chat/ChatWindow.tsx`** — `GET /api/tools` and the "
    "collection scope a 문서 검색 row sets."
)
add(
    between(
        "frontend/components/chat/ChatWindow.tsx",
        "  /** The 문서 검색 rows of the `@` menu.",
        "\n  function chooseModel(",
        lang="tsx",
    )
)

# ---------------------------------------------------------------- Task 3
add(
    """
---

### Task 3: the graph editor

**Goal.** `/workflows` draws the graph, edits it from the keyboard, saves a version, rolls one back,
and puts the server's refusal on the node or the edge it is about.

**The one thing to get right.** The edges now drive execution, so the picture is a claim about
behaviour. Everything else follows from that: coordinates are stored because a person arranged them,
`input`/`answer` cannot be deleted because a graph without them saves and will not run, and the
canvas's old paragraph about deliberately having no seat for drawing an order is DELETED rather than
softened.
"""
)
add("- [ ] **Step 1: Write `frontend/lib/graph.ts`**")
add(whole("frontend/lib/graph.ts"))
add("- [ ] **Step 2: Write `frontend/lib/graph.test.ts`**")
add(whole("frontend/lib/graph.test.ts"))
add("- [ ] **Step 3: Write `frontend/components/workflows/GraphEditor.tsx`**")
add(whole("frontend/components/workflows/GraphEditor.tsx", lang="tsx"))
add("- [ ] **Step 4: Write `frontend/app/(app)/workflows/page.tsx`**")
add(whole("frontend/app/(app)/workflows/page.tsx", lang="tsx"))
add(
    "- [ ] **Step 5: Modify `frontend/components/workflows/WorkflowCanvas.tsx`** — delete the "
    "paragraph that says there is deliberately no seat for drawing an order. It was true when the "
    "executor read no edges; it became false the moment one did."
)
add(
    between(
        "frontend/components/workflows/WorkflowCanvas.tsx",
        "      {/* Where the order lives now, said once,",
        "\n      {openKey && (",
        lang="tsx",
    )
)
add(
    "- [ ] **Step 6: Modify `frontend/components/chat/TraceDialog.tsx`** — one trace shape whoever "
    "authored the graph, and a node line that does not tell the 답변 node it searched the corpus."
)
add(
    between(
        "frontend/components/chat/TraceDialog.tsx",
        "/** The node kinds, for the line under a step.",
        "\n/** The graph behind an answer",
        lang="tsx",
    )
)
add("- [ ] **Step 7: Modify `frontend/package.json` — the two new test files**")
add(between("frontend/package.json", '    "test":', "\n  },", lang="json"))
add("- [ ] **Step 8: Modify `scripts/check_all_plans.py`**")
add(between("scripts/check_all_plans.py", "PLANS = [", "\nmissing = [", lang="python"))
add(
    "- [ ] **Step 9: Modify `docs/화면.md`** — the new sections, and the correction to the one this "
    "slice falsified. The old 에이전트 생성 section stays as history with its claim struck through: "
    "*선을 잇는 자리는 일부러 두지 않았습니다* was true while nothing read the edges, and this slice "
    "is what made it false."
)
add(
    between(
        "docs/화면.md",
        "## 워크플로우 — 간선이 실행 순서를 정합니다",
        "\n### 그래프",
        lang="markdown",
    )
)

# ---------------------------------------------------------------- Task 4
add(
    """
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
"""
)
add(
    "- [ ] **Step 1: Run** `cd frontend && npx tsc --noEmit && npm test && npm run build`, then "
    "`docker compose build frontend` and `docker compose up -d --force-recreate --no-deps frontend` "
    "— the rebuild is not enough on this machine; the container has to be recreated, and the served "
    "bundle grepped for a string only the new build has."
)

OUT.write_text("\n\n".join(PARTS) + "\n", encoding="utf-8")
print(f"wrote {OUT} ({len(OUT.read_text(encoding='utf-8').splitlines())} lines)")
