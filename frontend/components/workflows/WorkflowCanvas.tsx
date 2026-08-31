"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { AnswerModel, Collection, McpToolOption, PromptSummary } from "@/lib/types";

/** The agent builder canvas.
 *
 * WHAT THIS CANVAS IS NOT: a node graph whose edges decide execution. MOPAN
 * does not run a user-drawn graph. With 슈퍼 에이전트 on, a planner decides the
 * steps per question and `PlanStep.depends_on` is written by that planner, not
 * by an admin; with it off, retrieval is a fixed pipeline. So an edge an admin
 * could drag would drive nothing, and the first time they reordered two boxes
 * and the answer did not change, every other thing on this screen would stop
 * being believable too. There is therefore no edge tool, no port, and no
 * free x/y position - the `agents` schema has nowhere to store one anyway, so a
 * dragged position would silently vanish on save.
 *
 * WHAT IT IS: three fixed stages that mirror what the engine actually does -
 * the question arrives, the agent reaches for evidence within a boundary, a
 * model answers with a prompt - and a set of modules that are placed into the
 * middle two. Every module on this canvas is one field or one join row of the
 * SAME `agents` object the old form edited:
 *
 *   문서 분류 module   -> one row of agent_collections -> ResolvedAgent.collection_ids
 *   MCP 도구 module    -> one row of agent_tools       -> ResolvedAgent.tool_ids
 *   답변 지침 module   -> agents.prompt_name
 *   답변 모델 module   -> agents.answer_model
 *   슈퍼 에이전트 module -> agents.orchestrator
 *
 * THE EMPTY LANE IS THE DANGEROUS ONE. An empty collection list means
 * UNRESTRICTED, not "no access" - see app/agents/service.py. On a canvas an
 * empty lane reads as "nothing granted", which is the exact opposite, so an
 * empty group does not render as empty: it renders a full-width card that says
 * 전체 허용 out loud. That card is the whole reason this screen can claim to
 * make the permission boundary visible.
 *
 * DRAG IS A BONUS, NEVER THE ROUTE. Every module is a <button> in a list: Enter
 * places it, Enter on the placed card opens its settings, and 제거 takes it
 * away. Native HTML5 drag is layered on top for a mouse and costs four
 * handlers. Each lane also carries a native <details> holding the plain
 * checkbox list, so the pre-canvas way of working never disappeared.
 *
 * No canvas or diagram library. This is CSS grid and cards; react-flow would be
 * ~50x the runtime weight of the thing it draws, and it exists to give you the
 * edges this screen deliberately does not have.
 */

export type Draft = {
  name: string;
  description: string;
  prompt_name: string;
  answer_model: string;
  orchestrator: boolean;
  enabled: boolean;
  collection_ids: string[];
  tool_ids: string[];
};

export type Catalog = {
  collections: Collection[];
  tools: McpToolOption[];
  prompts: PromptSummary[];
  models: AnswerModel[];
};

const RISK_LABEL: Record<string, string> = {
  read: "읽기",
  write: "쓰기",
  destructive: "파괴적",
};

type Kind = "orchestrator" | "collection" | "tool" | "prompt" | "model";

/** One placeable thing. `key` is what a drag carries and what focus is restored
 * to, and it is derived from the id so it survives a refetch. */
type Module = {
  key: string;
  kind: Kind;
  group: GroupId;
  title: string;
  subtitle: string;
  /** 답변 지침 is not removable: an agent always answers with SOME prompt, and
   * `prompt_name` is NOT NULL. Its module is placed from birth and only
   * configured. */
  fixed?: boolean;
};

type GroupId = "mode" | "collections" | "tools" | "answer";

const MODULE_TYPE_LABEL: Record<Kind, string> = {
  orchestrator: "실행 방식",
  collection: "문서 분류",
  tool: "MCP 도구",
  prompt: "답변 지침",
  model: "답변 모델",
};

function collectionModule(c: Collection): Module {
  return {
    key: `collection:${c.id}`,
    kind: "collection",
    group: "collections",
    title: c.name,
    subtitle: c.description?.trim() || "설명 없음",
  };
}

function toolModule(t: McpToolOption): Module {
  return {
    key: `tool:${t.id}`,
    kind: "tool",
    group: "tools",
    title: `${t.server_name}/${t.name}`,
    subtitle: `위험도 ${RISK_LABEL[t.risk_level] ?? t.risk_level}`,
  };
}

const ORCHESTRATOR_MODULE: Module = {
  key: "orchestrator",
  kind: "orchestrator",
  group: "mode",
  title: "슈퍼 에이전트",
  subtitle: "질문마다 계획을 세워 검색과 도구를 나눠 실행합니다.",
};

function promptModule(draft: Draft): Module {
  return {
    key: "prompt",
    kind: "prompt",
    group: "answer",
    title: "답변 지침",
    subtitle: draft.prompt_name,
    fixed: true,
  };
}

function modelModule(draft: Draft, catalog: Catalog): Module {
  const model = catalog.models.find((m) => m.id === draft.answer_model);
  return {
    key: "model",
    kind: "model",
    group: "answer",
    title: "답변 모델",
    subtitle: model?.label ?? draft.answer_model,
  };
}

/** What is on the canvas right now, derived from the draft. There is no second
 * source of truth for the layout - lose this derivation and the canvas cannot
 * disagree with what will be saved. */
function placedModules(draft: Draft, catalog: Catalog): Module[] {
  const out: Module[] = [];
  if (draft.orchestrator) out.push(ORCHESTRATOR_MODULE);
  for (const c of catalog.collections) {
    if (draft.collection_ids.includes(c.id)) out.push(collectionModule(c));
  }
  for (const t of catalog.tools) {
    if (draft.tool_ids.includes(t.id)) out.push(toolModule(t));
  }
  out.push(promptModule(draft));
  if (draft.answer_model) out.push(modelModule(draft, catalog));
  return out;
}

/** The drawer: everything not yet placed. Sorted by the catalogue's own order,
 * which is name order from the server - deliberately NOT the order they were
 * added, because a position on this canvas must never look like a sequence. */
function drawerModules(draft: Draft, catalog: Catalog): Module[] {
  const out: Module[] = [];
  if (!draft.orchestrator) out.push(ORCHESTRATOR_MODULE);
  for (const c of catalog.collections) {
    if (!draft.collection_ids.includes(c.id)) out.push(collectionModule(c));
  }
  for (const t of catalog.tools) {
    if (!draft.tool_ids.includes(t.id)) out.push(toolModule(t));
  }
  if (!draft.answer_model) out.push(modelModule({ ...draft, answer_model: "" }, catalog));
  return out;
}

/** Placing a module = granting the capability. Every branch writes one field of
 * the same draft the old form wrote, which is what keeps the API unchanged. */
function place(draft: Draft, key: string, catalog: Catalog): Draft {
  if (key === "orchestrator") return { ...draft, orchestrator: true };
  if (key === "model") {
    // A model module with no model chosen yet defaults to the deployment
    // default, so placing it is never a save that means nothing.
    const fallback = catalog.models.find((m) => m.is_default) ?? catalog.models[0];
    return { ...draft, answer_model: draft.answer_model || fallback?.id || "" };
  }
  if (key.startsWith("collection:")) {
    const id = key.slice("collection:".length);
    return draft.collection_ids.includes(id)
      ? draft
      : { ...draft, collection_ids: [...draft.collection_ids, id] };
  }
  if (key.startsWith("tool:")) {
    const id = key.slice("tool:".length);
    return draft.tool_ids.includes(id) ? draft : { ...draft, tool_ids: [...draft.tool_ids, id] };
  }
  return draft;
}

/** Removing a module = removing the capability. 답변 지침 has no branch here on
 * purpose; it is `fixed` and its card renders no 제거. */
function remove(draft: Draft, key: string): Draft {
  if (key === "orchestrator") return { ...draft, orchestrator: false };
  if (key === "model") return { ...draft, answer_model: "" };
  if (key.startsWith("collection:")) {
    const id = key.slice("collection:".length);
    return { ...draft, collection_ids: draft.collection_ids.filter((x) => x !== id) };
  }
  if (key.startsWith("tool:")) {
    const id = key.slice("tool:".length);
    return { ...draft, tool_ids: draft.tool_ids.filter((x) => x !== id) };
  }
  return draft;
}

/** The chevron between two stages. Decorative: it says the question comes
 * before the evidence which comes before the answer, which is what
 * `retrieve()` then `answer()` actually do. It is not a connector anybody can
 * draw, move or delete, and there is no second one to compare it against.
 *
 * The stages run TOP TO BOTTOM, not left to right. Three lanes side by side put
 * a module card in a 190px column at 1280px, where 현장관측/water_quality
 * truncated to 현장관측/wat… - measured, not guessed. A full-width band lets the
 * cards wrap and reads identically at 390px, where the columns would have had
 * to stack anyway. */
function Flow() {
  return (
    <div className="flex items-center justify-center" aria-hidden="true">
      <svg
        viewBox="0 0 24 24"
        className="h-5 w-5 rotate-90 text-on-surface-variant"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M5 12h13" />
        <path d="m13 7 5 5-5 5" />
      </svg>
    </div>
  );
}

/** The one card on the canvas that is not a module and cannot be removed: the
 * question, which is where every run starts. */
function QuestionStage() {
  return (
    <section
      aria-labelledby="stage-question"
      className="rounded-md bg-surface-container-low p-4"
    >
      <h3 id="stage-question" className="text-label font-medium text-on-surface-variant">
        1. 질문
      </h3>
      <div className="mt-2 rounded-md bg-surface-container px-4 py-3">
        <p className="text-body font-medium text-on-surface">사용자의 질문</p>
        <p className="mt-1 text-caption text-on-surface-variant">
          채팅에서 이 에이전트를 고르고 질문하면 여기서 시작합니다. 이 자리는 설정할 것이 없어서
          모듈이 아닙니다.
        </p>
      </div>
    </section>
  );
}

function ModuleCard({
  module,
  onOpen,
  onRemove,
  onDragStart,
}: {
  module: Module;
  onOpen: () => void;
  onRemove: () => void;
  onDragStart: (event: React.DragEvent) => void;
}) {
  return (
    <li
      draggable
      onDragStart={onDragStart}
      className="w-full rounded-md bg-surface-container transition-colors duration-150 hover:bg-surface-container-high sm:w-64"
    >
      <div className="flex items-start gap-2 p-3">
        <button
          type="button"
          onClick={onOpen}
          data-focus-key={module.key}
          title={module.title}
          className="min-w-0 flex-1 rounded-sm text-left"
        >
          {/* Suppressed when it would repeat the title: 답변 모델 / 답변 모델 /
              GPT-4o mini is three lines saying two things. */}
          {MODULE_TYPE_LABEL[module.kind] !== module.title && (
            <span className="block text-caption text-on-surface-variant">
              {MODULE_TYPE_LABEL[module.kind]}
            </span>
          )}
          <span className="block truncate text-body font-medium text-on-surface">
            {module.title}
          </span>
          <span className="block truncate text-caption text-on-surface-variant">
            {module.subtitle}
          </span>
          <span className="mt-1 block text-caption text-primary">설정 열기</span>
        </button>
        {!module.fixed && (
          <button
            type="button"
            onClick={onRemove}
            aria-label={`${module.title} 모듈 제거`}
            className="icon-btn h-8 w-8 shrink-0"
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
    </li>
  );
}

/** A group of modules inside a stage, with the sentence its EMPTY state has to
 * say. `emptyMeans` is a required parameter and not a default for the same
 * reason the old form's `Choices` made it one: the empty case is the one that
 * can mislead, so no call site gets to forget it. */
function Group({
  id,
  label,
  help,
  emptyMeans,
  modules,
  onDropKey,
  children,
}: {
  id: GroupId;
  label: string;
  help: string;
  emptyMeans: string | null;
  modules: Module[];
  onDropKey: (key: string, group: GroupId) => void;
  children?: React.ReactNode;
}) {
  const [over, setOver] = useState(false);
  const headingId = `group-${id}`;
  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        const key = event.dataTransfer.getData("text/plain");
        if (key) onDropKey(key, id);
      }}
      className={`rounded-md p-3 transition-colors duration-150 ${
        over ? "bg-surface-container-high" : "bg-surface-container-lowest"
      }`}
    >
      <h4 id={headingId} className="text-label font-medium text-on-surface">
        {label}
      </h4>
      <p className="mt-1 text-caption text-on-surface-variant">{help}</p>
      {modules.length > 0 ? (
        <ul aria-labelledby={headingId} className="mt-2 flex flex-wrap gap-2">
          {children}
        </ul>
      ) : (
        <p className="mt-2 rounded-sm bg-surface-container px-3 py-3 text-caption text-primary">
          {/* 전체 허용, never 없음. An empty lane on a canvas looks like a closed
              door; for these two lists it is an open one. */}
          {emptyMeans}
        </p>
      )}
    </div>
  );
}

export default function AgentCanvas({
  draft,
  onChange,
  catalog,
}: {
  draft: Draft;
  onChange: (next: Draft) => void;
  catalog: Catalog;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const uid = useId();
  const [openKey, setOpenKey] = useState<string | null>(null);
  // Focus follows the module, not the button that vanished. Placing a module
  // removes its drawer button from the DOM, so without this the caret falls to
  // <body> and a keyboard user loses their place on every single add.
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (!focusKey) return;
    rootRef.current
      ?.querySelector<HTMLElement>(`[data-focus-key="${CSS.escape(focusKey)}"]`)
      ?.focus();
    setFocusKey(null);
  }, [focusKey, draft]);

  const placed = placedModules(draft, catalog);
  const drawer = drawerModules(draft, catalog);
  const byGroup = (group: GroupId) => placed.filter((m) => m.group === group);

  function add(module: Module) {
    onChange(place(draft, module.key, catalog));
    setFocusKey(module.key);
    setStatus(`${module.title} 모듈을 캔버스에 놓았습니다.`);
  }

  function drop(module: Module) {
    onChange(remove(draft, module.key));
    // The drawer button, not the card: the card is the thing that just stopped
    // existing. Removing a module with the keyboard has to leave the caret on
    // the control that puts it back.
    setFocusKey(`drawer:${module.key}`);
    setStatus(`${module.title} 모듈을 캔버스에서 뺐습니다.`);
  }

  /** A drop onto the group the module belongs to. A module dropped into the
   * wrong group is ignored rather than relocated: the groups are not
   * interchangeable slots, they are what the module IS. */
  function onDropKey(key: string, group: GroupId) {
    const module = [...drawer, ...placed].find((m) => m.key === key);
    if (!module || module.group !== group) return;
    if (drawer.some((m) => m.key === key)) add(module);
  }

  function onDrawerDrop(event: React.DragEvent) {
    event.preventDefault();
    const key = event.dataTransfer.getData("text/plain");
    const module = placed.find((m) => m.key === key);
    if (module && !module.fixed) drop(module);
  }

  const cards = (group: GroupId) =>
    byGroup(group).map((module) => (
      <ModuleCard
        key={module.key}
        module={module}
        onOpen={() => setOpenKey(module.key)}
        onRemove={() => drop(module)}
        onDragStart={(event) => event.dataTransfer.setData("text/plain", module.key)}
      />
    ));

  const restriction =
    draft.collection_ids.length === 0
      ? "전체 분류"
      : `분류 ${draft.collection_ids.length}개만`;
  const toolRestriction =
    draft.tool_ids.length === 0 ? "전체 도구" : `도구 ${draft.tool_ids.length}개만`;

  return (
    <div ref={rootRef} className="space-y-3">
      {/* The boundary, in one line, before anything else. An admin who reads
          nothing else on this screen has still read what this agent may touch. */}
      <div className="flex flex-wrap items-center gap-2 rounded-md bg-surface-container-high px-4 py-3">
        <span className="text-label font-medium text-on-surface">허용 범위</span>
        <span className="rounded-full bg-surface-container-lowest px-3 py-1 text-caption text-on-surface">
          {restriction}
        </span>
        <span className="rounded-full bg-surface-container-lowest px-3 py-1 text-caption text-on-surface">
          {toolRestriction}
        </span>
        <span className="rounded-full bg-surface-container-lowest px-3 py-1 text-caption text-on-surface">
          {draft.orchestrator ? "슈퍼 에이전트 켬" : "슈퍼 에이전트 끔"}
        </span>
      </div>

      {/* The drawer. Also the drop target that means "remove". */}
      <section
        aria-labelledby={`${uid}-drawer`}
        onDragOver={(event) => event.preventDefault()}
        onDrop={onDrawerDrop}
        className="rounded-md bg-surface-container-low p-4"
      >
        <h3 id={`${uid}-drawer`} className="text-label font-medium text-on-surface-variant">
          모듈 서랍
        </h3>
        <p className="mt-1 text-caption text-on-surface-variant">
          누르거나 끌어다 캔버스에 놓으면 그 기능이 켜집니다. 캔버스에서 여기로 끌어다 놓으면
          빠집니다.
        </p>
        {drawer.length === 0 ? (
          <p className="mt-3 text-body text-on-surface-variant">
            놓을 수 있는 모듈을 모두 놓았습니다.
          </p>
        ) : (
          <ul className="mt-3 flex flex-wrap gap-2">
            {drawer.map((module) => (
              <li key={module.key}>
                <button
                  type="button"
                  draggable
                  onDragStart={(event) => event.dataTransfer.setData("text/plain", module.key)}
                  onClick={() => add(module)}
                  data-focus-key={`drawer:${module.key}`}
                  className="flex max-w-full flex-col items-start rounded-md bg-surface-container px-3 py-2 text-left transition-colors duration-150 hover:bg-surface-container-high"
                >
                  {MODULE_TYPE_LABEL[module.kind] !== module.title && (
                    <span className="text-caption text-on-surface-variant">
                      {MODULE_TYPE_LABEL[module.kind]}
                    </span>
                  )}
                  <span className="max-w-[16rem] truncate text-body text-on-surface">
                    + {module.title}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {/* In the DOM from first render, not mounted on demand: a live region
            added at the same moment its text arrives is not announced. */}
        <p role="status" aria-live="polite" className="mt-2 text-caption text-primary">
          {status}
        </p>
      </section>

      {/* Three stages, top to bottom, each the full width of the builder. The
          ORDER of the bands is the pipeline; nothing else on this canvas
          carries a position that means anything. */}
      <div className="space-y-2">
        <QuestionStage />
        <Flow />

        <section
          aria-labelledby={`${uid}-reach`}
          className="rounded-md bg-surface-container-low p-4"
        >
          <h3 id={`${uid}-reach`} className="text-label font-medium text-on-surface-variant">
            2. 이 에이전트가 닿을 수 있는 것
          </h3>
          <div className="mt-2 space-y-2">
            <Group
              id="mode"
              label="실행 방식"
              help={
                draft.orchestrator
                  ? "질문마다 플래너가 계획을 세워 아래 분류와 도구를 골라 씁니다."
                  : "계획 없이 문서 검색을 한 번 합니다. 도구는 사용자가 채팅에서 직접 고를 때만 호출됩니다."
              }
              emptyMeans="슈퍼 에이전트를 놓지 않았습니다. 계획 없이 문서 검색만 합니다."
              modules={byGroup("mode")}
              onDropKey={onDropKey}
            >
              {cards("mode")}
            </Group>

            <Group
              id="collections"
              label="문서 분류"
              help="여기 놓인 분류에서만 근거를 찾습니다. 계획을 세울 때도 여기 없는 분류는 이름조차 보이지 않습니다."
              emptyMeans="분류를 하나도 놓지 않았습니다. 제한이 아니라 전체 분류 허용입니다."
              modules={byGroup("collections")}
              onDropKey={onDropKey}
            >
              {cards("collections")}
            </Group>
            <details className="rounded-md bg-surface-container-lowest px-3 py-2">
              <summary className="cursor-pointer text-caption text-primary">
                분류를 체크박스로 고르기
              </summary>
              <PlainChoices
                idPrefix={`${uid}-c`}
                options={catalog.collections.map((c) => ({ id: c.id, label: c.name }))}
                selected={draft.collection_ids}
                onToggle={(id) =>
                  onChange(
                    draft.collection_ids.includes(id)
                      ? remove(draft, `collection:${id}`)
                      : place(draft, `collection:${id}`, catalog),
                  )
                }
              />
            </details>

            <Group
              id="tools"
              label="MCP 도구"
              help="여기 놓인 도구만 부를 수 있습니다. 목록 밖의 도구를 지정한 실행 계획은 일부만 걸러내는 것이 아니라 통째로 거부됩니다."
              emptyMeans="도구를 하나도 놓지 않았습니다. 제한이 아니라 전체 도구 허용입니다."
              modules={byGroup("tools")}
              onDropKey={onDropKey}
            >
              {cards("tools")}
            </Group>
            <details className="rounded-md bg-surface-container-lowest px-3 py-2">
              <summary className="cursor-pointer text-caption text-primary">
                도구를 체크박스로 고르기
              </summary>
              <PlainChoices
                idPrefix={`${uid}-t`}
                options={catalog.tools.map((t) => ({
                  id: t.id,
                  label: `${t.server_name}/${t.name}`,
                }))}
                selected={draft.tool_ids}
                onToggle={(id) =>
                  onChange(
                    draft.tool_ids.includes(id)
                      ? remove(draft, `tool:${id}`)
                      : place(draft, `tool:${id}`, catalog),
                  )
                }
              />
            </details>
          </div>
        </section>

        <Flow />

        <section
          aria-labelledby={`${uid}-answer`}
          className="rounded-md bg-surface-container-low p-4"
        >
          <h3 id={`${uid}-answer`} className="text-label font-medium text-on-surface-variant">
            3. 답변
          </h3>
          <div className="mt-2">
            <Group
              id="answer"
              label="답변 구성"
              help="찾은 근거를 이 지침과 이 모델로 답으로 만듭니다."
              emptyMeans={null}
              modules={byGroup("answer")}
              onDropKey={onDropKey}
            >
              {cards("answer")}
            </Group>
          </div>
        </section>
      </div>

      {/* Said out loud, because its absence is the honest part of this screen. */}
      <p className="rounded-md bg-surface-container px-4 py-3 text-caption text-on-surface-variant">
        모듈 사이에 선을 잇는 자리는 일부러 두지 않았습니다. 실행 순서는 질문마다 정해지므로 —
        슈퍼 에이전트를 켜면 플래너가, 끄면 고정된 검색 한 번이 정합니다 — 여기서 순서를 그려도
        그림만 바뀌고 동작은 그대로입니다. 모듈이 놓인 자리와 순서에는 아무 뜻이 없습니다.
      </p>

      {openKey && (
        <ModuleDialog
          moduleKey={openKey}
          draft={draft}
          catalog={catalog}
          onChange={onChange}
          onRemove={() => {
            const module = placed.find((m) => m.key === openKey);
            setOpenKey(null);
            if (module) drop(module);
          }}
          onClose={() => {
            setOpenKey(null);
            setFocusKey(openKey);
          }}
        />
      )}
    </div>
  );
}

/** The pre-canvas way of choosing, kept alive under a native <details>.
 * A canvas must not be the only route to a working agent, and this is the
 * cheapest honest way to keep the other one: the same two functions the cards
 * call, so there is no second source of truth to drift. */
function PlainChoices({
  idPrefix,
  options,
  selected,
  onToggle,
}: {
  idPrefix: string;
  options: { id: string; label: string }[];
  selected: string[];
  onToggle: (id: string) => void;
}) {
  if (options.length === 0) {
    return <p className="mt-2 text-caption text-on-surface-variant">고를 항목이 없습니다.</p>;
  }
  return (
    <div className="mt-2 space-y-1">
      {options.map((option) => (
        <label
          key={option.id}
          htmlFor={`${idPrefix}-${option.id}`}
          className="flex items-center gap-2 text-body text-on-surface"
        >
          <input
            id={`${idPrefix}-${option.id}`}
            type="checkbox"
            checked={selected.includes(option.id)}
            onChange={() => onToggle(option.id)}
            className="h-4 w-4 shrink-0 accent-primary"
          />
          <span className="min-w-0 truncate">{option.label}</span>
        </label>
      ))}
    </div>
  );
}

/** One module's settings, and nothing else's.
 *
 * A native <dialog> + showModal(), the same pattern ConfirmDialog uses: focus
 * trap, Escape, an inert background and top-layer stacking come with it. A
 * side panel would have had to grow all of that by hand and would have been the
 * one part of this screen that broke at 390px.
 */
function ModuleDialog({
  moduleKey,
  draft,
  catalog,
  onChange,
  onRemove,
  onClose,
}: {
  moduleKey: string;
  draft: Draft;
  catalog: Catalog;
  onChange: (next: Draft) => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialogRef.current?.showModal();
  }, []);

  const collection =
    moduleKey.startsWith("collection:") &&
    catalog.collections.find((c) => c.id === moduleKey.slice("collection:".length));
  const tool =
    moduleKey.startsWith("tool:") &&
    catalog.tools.find((t) => t.id === moduleKey.slice("tool:".length));

  let title = "모듈 설정";
  let body: React.ReactNode = null;
  let removable = true;

  if (moduleKey === "orchestrator") {
    title = "슈퍼 에이전트";
    body = (
      <div className="space-y-3 text-body text-on-surface-variant">
        <p className="text-on-surface">
          질문마다 플래너가 실행 계획을 세웁니다. 계획의 각 단계는 문서 검색이거나 도구 호출이고,
          서로 의존하지 않는 단계는 동시에 실행됩니다.
        </p>
        <p>
          계획이 이 에이전트에 없는 도구를 지정하면 그 단계만 빠지는 것이 아니라 계획이 통째로
          거부되고, 평범한 문서 검색으로 답합니다.
        </p>
        <p>
          <strong className="text-on-surface">단계 순서는 여기서 정하지 않습니다.</strong> 질문을
          받은 뒤 플래너가 정하므로 이 화면에는 순서를 그리는 자리가 없습니다.
        </p>
        <p>이 모듈을 빼면 계획 없이 문서 검색을 한 번 하고 답합니다.</p>
      </div>
    );
  } else if (moduleKey === "prompt") {
    removable = false;
    title = "답변 지침";
    const active = catalog.prompts.find((p) => p.name === draft.prompt_name);
    const names = catalog.prompts.some((p) => p.name === draft.prompt_name)
      ? catalog.prompts.map((p) => p.name)
      : [draft.prompt_name, ...catalog.prompts.map((p) => p.name)];
    body = (
      <div className="space-y-3">
        <label htmlFor="module-prompt" className="block text-label font-medium text-on-surface-variant">
          프롬프트 이름
        </label>
        <select
          id="module-prompt"
          value={draft.prompt_name}
          onChange={(event) => onChange({ ...draft, prompt_name: event.target.value })}
          className="field w-full"
        >
          {names.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <p className="text-caption text-on-surface-variant">
          프롬프트 관리에서 만든 이름입니다. 내용을 고치면 이 에이전트의 답변도 바로 바뀝니다.
          그래서 이 모듈은 뺄 수 없습니다 — 답변에는 언제나 지침이 하나 있습니다.
        </p>
        {active && (
          <div className="max-h-40 overflow-y-auto rounded-sm bg-surface-container p-3">
            <p className="whitespace-pre-wrap text-caption text-on-surface-variant">
              {active.text.slice(0, 800)}
              {active.text.length > 800 && " …"}
            </p>
          </div>
        )}
      </div>
    );
  } else if (moduleKey === "model") {
    title = "답변 모델";
    body = (
      <div className="space-y-3">
        <label htmlFor="module-model" className="block text-label font-medium text-on-surface-variant">
          이 에이전트가 쓸 모델
        </label>
        <select
          id="module-model"
          value={draft.answer_model}
          onChange={(event) => onChange({ ...draft, answer_model: event.target.value })}
          className="field w-full"
        >
          {catalog.models.map((model) => (
            <option key={model.id} value={model.id}>
              {model.label}
              {model.is_default ? " (배포 기본값)" : ""}
            </option>
          ))}
        </select>
        <p className="text-caption text-on-surface-variant">
          채팅에서 모델을 직접 고르면 그쪽이 우선합니다. 이 모듈을 빼면 배포 기본값으로 답합니다.
        </p>
      </div>
    );
  } else if (collection) {
    title = collection.name;
    body = (
      <div className="space-y-3 text-body text-on-surface-variant">
        <p className="text-on-surface">{collection.description?.trim() || "설명 없는 분류입니다."}</p>
        <p>
          이 모듈이 놓여 있으면 이 에이전트의 검색은 여기 놓인 분류 안에서만 이루어집니다. 사용자가
          채팅에서 다른 분류를 지정해도 허용되지 않습니다.
        </p>
        <p className="text-primary">
          분류 모듈을 하나도 놓지 않으면 제한이 걸리지 않고 전체 분류를 허용합니다.
        </p>
      </div>
    );
  } else if (tool) {
    title = `${tool.server_name}/${tool.name}`;
    const properties = Object.keys(
      (tool.input_schema?.properties as Record<string, unknown> | undefined) ?? {},
    );
    body = (
      <div className="space-y-3 text-body text-on-surface-variant">
        <p className="text-on-surface">{tool.description?.trim() || "설명 없는 도구입니다."}</p>
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-caption">
          <dt className="text-on-surface-variant">서버</dt>
          <dd className="text-on-surface">{tool.server_name}</dd>
          <dt className="text-on-surface-variant">위험도</dt>
          <dd className="text-on-surface">{RISK_LABEL[tool.risk_level] ?? tool.risk_level}</dd>
          <dt className="text-on-surface-variant">입력</dt>
          <dd className="text-on-surface">{properties.join(", ") || "없음"}</dd>
        </dl>
        <p>
          호출 인자는 여기서 정하지 않습니다. 슈퍼 에이전트를 켜면 플래너가, 끄면 사용자가 채팅에서
          직접 채웁니다. 이 모듈이 정하는 것은 &ldquo;부를 수 있는가&rdquo; 하나입니다.
        </p>
        <p className="text-primary">
          도구 모듈을 하나도 놓지 않으면 제한이 걸리지 않고 전체 도구를 허용합니다.
        </p>
      </div>
    );
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="module-dialog-title"
      onClose={onClose}
      className="w-full max-w-md rounded-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim"
    >
      <div className="p-6">
        <h2 id="module-dialog-title" className="text-title font-medium">
          {title}
        </h2>
        <div className="mt-4">{body}</div>
        <div className="mt-6 flex justify-between gap-2">
          {removable ? (
            <button type="button" onClick={onRemove} className="btn-danger btn-compact">
              모듈 제거
            </button>
          ) : (
            <span />
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
