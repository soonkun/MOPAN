"use client";

import { useEffect, useRef, useState } from "react";
import WorkflowPicker, { DEFAULT_WORKFLOW_LABEL } from "@/components/chat/WorkflowPicker";
import AttachmentChip from "@/components/chat/AttachmentChip";
import MentionMenu from "@/components/chat/MentionMenu";
import ModelPicker from "@/components/chat/ModelPicker";
import PopoverSheet from "@/components/chat/PopoverSheet";
import Switch from "@/components/ui/Switch";
import {
  filterEntries,
  mentionAt,
  mentionEntries,
  type MentionEntry,
} from "@/lib/mention";
import type {
  AnswerModel,
  Attachment,
  CallableTool,
  McpToolOption,
  PendingToolCall,
  WorkflowOption,
} from "@/lib/types";

/** One file the user has chosen. It exists on screen from the moment it is
 * picked, before POST /api/attachments has answered, so that a refusal can be
 * rendered on the chip it belongs to rather than as a page-level banner. */
export type PendingAttachment = {
  /** Local, and NOT the attachment id: a chip exists before the server has
   * given it one, and an upload that is refused never gets one at all. */
  key: string;
  filename: string;
  sizeBytes: number;
  kind: "image" | "document";
  /** A blob: URL made from the File, so an image thumbnail appears immediately
   * and without a second round trip. Revoked by ChatWindow. */
  previewUrl: string | null;
  status: "uploading" | "ready" | "error";
  /** The server's row, once POST /api/attachments has answered. It is what the
   * send carries (its id) and what the sent user turn renders from. */
  attachment: Attachment | null;
  error: string | null;
};

// backend/app/documents/validation.py: ALLOWED_EXTENSIONS | IMAGE_EXTENSIONS.
export const ATTACHMENT_EXTENSIONS = [
  "pdf",
  "docx",
  "txt",
  "md",
  "html",
  "png",
  "jpg",
  "jpeg",
  "webp",
  "gif",
];
const ACCEPT = ATTACHMENT_EXTENSIONS.map((ext) => `.${ext}`).join(",");

// 8 rows of body-lg (26px) plus the textarea's own 8px padding top and bottom.
const MAX_HEIGHT = 8 * 26 + 16;

/** Exactly one overlay is open at a time, and which one is a single value.
 *
 * Two states would let the + menu and a picker be open together, which is the
 * bug the hand-off was written to avoid: two stacked sheets over a composer,
 * two Escapes to get out, and a scrim over a scrim. */
type Sheet = null | "menu" | "model" | "workflow" | "mcp";

// The four-point spark. Plain currentColor, NOT the brand gradient: §2 reserves
// that for the wordmark, the assistant sparkle and the streaming indicator.
const SPARK = (
  <>
    <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" />
    <path d="M18 15l.8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8z" />
  </>
);
const SHIELD = (
  <>
    <path d="M12 3 4 7v5c0 4.4 3.2 8.2 8 9 4.8-.8 8-4.6 8-9V7l-8-4Z" />
    <path d="m9 12 2 2 4-4" />
  </>
);
// The processor die. The outer rectangle is load-bearing: without it the pins
// alone read as a snowflake, and in a menu row directly under the spark above
// the two glyphs were telling each other apart by nothing.
const CHIP = (
  <>
    <rect x="5" y="5" width="14" height="14" rx="2.5" />
    <rect x="9.5" y="9.5" width="5" height="5" rx="1" />
    <path d="M9.5 2v3M14.5 2v3M9.5 19v3M14.5 19v3M2 9.5h3M2 14.5h3M19 9.5h3M19 14.5h3" />
  </>
);
const CLIP = <path d="M17 8.5 9.4 16a2.5 2.5 0 0 0 3.6 3.6l7.1-7.1a4.5 4.5 0 0 0-6.4-6.4l-7 7a6.5 6.5 0 0 0 9.2 9.2l5.6-5.6" />;
// 플러그 - MCP 서버 행. "연결"의 은유.
const PLUG = (
  <>
    <path d="M9 2v5M15 2v5" />
    <path d="M7 7h10v4a5 5 0 0 1-5 5 5 5 0 0 1-5-5z" />
    <path d="M12 16v6" />
  </>
);
// 렌치(🔧) - 도구 설정 행. Lucide wrench: 직접 그린 것들은 안 읽혔다(소유자 지적).
const WRENCH = <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />;

function Glyph({ children, className = "h-5 w-5 shrink-0" }: { children: React.ReactNode; className?: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

/** One row of the + menu.
 *
 * `value` is the trailing label - the current model, the current agent, 켬/끔 -
 * and it is the reason a menu can hold a setting at all: a control that hides
 * what it is set to is worse than no control. The chips outside the menu carry
 * the same values for the state that has to be readable WITHOUT opening it. */
function MenuRow({
  icon,
  label,
  value,
  trailing,
  onClick,
  pressed,
  disabled,
  title,
}: {
  icon: React.ReactNode;
  label: string;
  value?: string;
  /** 값 대신 실물 컨트롤(스위치)을 달 때. `value`와 함께 오면 값이 앞선다. */
  trailing?: React.ReactNode;
  onClick: () => void;
  pressed?: boolean;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={pressed}
      disabled={disabled}
      title={title}
      className="flex w-full items-center gap-3 rounded-md px-3 py-3 text-left text-body text-on-surface transition-colors duration-150 hover:bg-surface-container-high disabled:cursor-default disabled:opacity-60 sm:py-2"
    >
      <Glyph className="h-5 w-5 shrink-0 text-on-surface-variant">{icon}</Glyph>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {value && (
        <span className="max-w-[8rem] shrink-0 truncate text-caption text-on-surface-variant">
          {value}
        </span>
      )}
      {!value && trailing}
    </button>
  );
}

/** A state chip in the composer, above the textarea.
 *
 * Not decoration: it is the answer to "which model is about to answer this" and
 * "is the super agent on", and both are questions asked BEFORE sending. Pressing
 * one opens the sheet that changes it, so the menu is not the only way in. */
function StateChip({
  icon,
  label,
  onClick,
  active,
  disabled,
  ariaLabel,
  title,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  ariaLabel: string;
  title?: string;
}) {
  return (
    <button
      type="button"
      // A pointer press that moves focus off the textarea dismisses the phone
      // keyboard under the user; the same rule every control in here keeps.
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      title={title}
      className={`inline-flex max-w-full items-center gap-1.5 rounded-full px-3 py-1.5 text-caption transition-colors duration-150 disabled:cursor-default ${
        active
          ? "bg-primary-container text-on-primary-container"
          : "bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest"
      }`}
    >
      <Glyph className="h-3.5 w-3.5 shrink-0">{icon}</Glyph>
      <span aria-hidden="true" className="truncate">
        {label}
      </span>
    </button>
  );
}

export default function Composer({
  value,
  onChange,
  onSubmit,
  onFiles,
  attachments,
  onRemove,
  sending,
  onStop,
  textareaRef,
  models,
  model,
  onModelChange,
  tools,
  callables,
  collectionId,
  onCollectionChange,
  toolCall,
  onToolSelect,
  onToolRemove,
  orchestrator,
  onOrchestratorChange,
  reasoningEffort,
  onReasoningEffortChange,
  mcpServers,
  onMcpServerChange,
  pinnedServers,
  onServerPin,
  onServerUnpin,
  workflows,
  workflowId,
  onWorkflowChange,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onFiles: (files: File[]) => void;
  attachments: PendingAttachment[];
  onRemove: (key: string) => void;
  sending: boolean;
  onStop: () => void;
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  models: AnswerModel[];
  model: string;
  onModelChange: (id: string) => void;
  /** GET /api/mcp/tools. Empty for a deployment with no MCP server registered,
   * which is what drops the 도구 사용 row from the menu rather than opening
   * nothing. */
  tools: McpToolOption[];
  /** GET /api/tools - everything callable, in one list, as `@` shows it. Empty
   * while it is still loading and for a request that failed, which costs the
   * user the menu and never the question. */
  callables: CallableTool[];
  /** The collection this question is scoped to, or null for the whole corpus.
   * Set by picking a 문서 검색 row in the `@` menu; there is no other control for
   * it, because scoping a search is a per-question thought rather than a
   * setting. */
  collectionId: string | null;
  onCollectionChange: (id: string | null) => void;
  /** ONE pending call. Slice 2 is manual invocation; a plan that runs several
   * steps is Slice 3, and the backend already accepts a list. */
  toolCall: PendingToolCall | null;
  onToolSelect: (call: PendingToolCall) => void;
  onToolRemove: () => void;
  /** 슈퍼 에이전트, chosen per question the way the model is. A toggle rather
   * than a third picker: there are two modes, and the direct RAG path is the
   * default until the planner measures better on the eval set.
   *
   * IT IS ONLY HERE NOW. `workflows.orchestrator` used to force it on for a
   * whole saved configuration - a stored PROCEDURE switching on autonomous
   * PLANNING - and that column is gone. So this toggle is never overridden, and
   * the disabled-but-on chip that used to say so is gone with it. */
  orchestrator: boolean;
  onOrchestratorChange: (value: boolean) => void;
  /** 서버 단위의 자동 사용 스위치, 클로드 데스크톱의 커넥터 토글 모양. 켜 두면
   * 상황이 맞을 때 모델이 그 서버의 도구를 알아서 쓰고, `@`로 직접 부르는
   * 길은 꺼져 있어도 열려 있다. 도구 단위가 아니라 서버 단위인 것이 요점이다 -
   * "서버를 연결하면 그 안의 기능은 다 쓰는 것"이라는 소유자의 말 그대로. */
  mcpServers: { name: string; on: boolean }[];
  onMcpServerChange: (name: string, on: boolean) => void;
  /** @로 이번 질문에 지목한 서버들. 토글이 꺼져 있어도 자동 사용 후보에
   * 들어가고, 전송과 함께 비워진다(첨부·도구 칩과 같은 수명). */
  pinnedServers: string[];
  onServerPin: (name: string) => void;
  onServerUnpin: (name: string) => void;
  /** 추론 모델의 사고 깊이. 모델 선택 시트 안의 즉시/중간/깊이가 그 집이다. */
  reasoningEffort: string;
  onReasoningEffortChange: (value: "minimal" | "low" | "medium" | "high") => void;
  /** GET /api/workflows/selectable. Empty for a deployment with no workflow
   * configured, which drops the row rather than offering a single 기본 row that
   * is not a choice. */
  workflows: WorkflowOption[];
  /** null is NO WORKFLOW - this app exactly as it behaved before workflows
   * existed. It is a real selection, not "nothing chosen yet". */
  workflowId: string | null;
  onWorkflowChange: (id: string | null) => void;
}) {
  const currentModel = models.find((m) => m.id === model) ?? models[0];
  const currentWorkflow = workflows.find((w) => w.id === workflowId) ?? null;

  const fileRef = useRef<HTMLInputElement>(null);
  const plusRef = useRef<HTMLButtonElement>(null);
  const [sheet, setSheet] = useState<Sheet>(null);
  // The `@…` being typed: where its `@` is, and what has been typed after it.
  // Null is "the menu is closed", and it is the ONLY thing that opens it.
  const [mention, setMention] = useState<{ start: number; query: string } | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const mentionId = "composer-mention-list";
  // Chrome fires keydown(Enter) with isComposing=true while a Hangul syllable
  // is still being composed, but not every engine does; this ref is the second
  // half of the same guard, set from the composition events themselves.
  const composingRef = useRef(false);

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

  // Auto-grow, 1 to 8 rows. height:auto first, or scrollHeight only ever
  // reports the height it already has and the box can never shrink again after
  // the user deletes a line.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [value, textareaRef]);

  /** Put the caret back in the textarea after the file sheet closes.
   *
   * Retaining focus (see the + button's onMouseDown) is what keeps the
   * keyboard up on the way IN. It does not bring it back on the way OUT: iOS
   * hides the keyboard for the duration of a system sheet regardless of who
   * holds focus. Re-focusing here runs inside the gesture the pick completed,
   * which is the one place the platform allows it.
   *
   * Guarded on the element still being there - a stream can unmount the
   * composer between opening the sheet and closing it. */
  function restoreKeyboard() {
    textareaRef.current?.focus();
  }

  // `cancel` on a file input - dismissing the sheet without choosing anything -
  // is attached natively because React's InputHTMLAttributes does not declare
  // onCancel, so the JSX prop is a type error rather than a listener. Without
  // this, backing out of the picker leaves a focused composer and no keyboard,
  // which is the same complaint as tapping + in the first place.
  useEffect(() => {
    const input = fileRef.current;
    if (!input) return;
    const onCancel = () => textareaRef.current?.focus();
    input.addEventListener("cancel", onCancel);
    return () => input.removeEventListener("cancel", onCancel);
  }, [textareaRef]);

  function take(list: FileList | null) {
    if (!list?.length) return;
    onFiles(Array.from(list));
  }

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
    } else if (entry.kind === "mcp" && entry.serverName) {
      // 서버 지목: 인자 입력 시트를 열지 않는다("도구 사용 창이 무슨 의미인지
      // 모르겠다"던 실사고의 그 창이다). 인자는 자동 사용의 숙고가 질문에서
      // 뽑고, 부족하면 되묻는다 - 지목은 그 후보에 이 서버를 토글과 무관하게
      // 넣는 일만 한다.
      onServerPin(entry.serverName);
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

  /** The ONE owner of focus return, for every sheet in this component.
   *
   * The + button is where focus lands, not the menu row that was pressed: by
   * the time a picker closes, the row that opened it has been unmounted with
   * the menu, and focusing a detached element silently drops the caret on
   * <body>. It is also why this is not inside PopoverSheet - the hand-off below
   * closes the menu WITHOUT calling this, so that the picker it is opening in
   * the same tick keeps the focus its showModal() just took. */
  function closeSheet() {
    setSheet(null);
    plusRef.current?.focus();
  }

  const entries = mentionEntries(callables, workflows, tools);
  const visible = mention ? filterEntries(entries, mention.query) : [];
  const active = visible[Math.min(activeIndex, visible.length - 1)];
  const currentCollection = callables
    .find((c) => c.kind === "rag")
    ?.collections.find((c) => c.id === collectionId);

  return (
    // §8: one surface-container block at --radius-xl, no border at rest, a 2px
    // primary outline on focus-within, thumbnails in a row above the textarea
    // and inside the same block.
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      className="rounded-xl bg-surface-container p-2 outline-primary transition-colors duration-150 focus-within:outline focus-within:outline-2"
    >
      {(toolCall || collectionId || pinnedServers.length > 0) && (
        <div className="flex flex-wrap gap-2 p-1 pb-2">
          {/* @로 지목한 MCP 서버. 이번 질문의 자동 사용 후보에 토글과 무관하게
              들어간다 - "꺼져 있어도 @면 적극 사용"의 실체. */}
          {pinnedServers.map((server) => (
            <span
              key={server}
              className="inline-flex max-w-full items-center gap-2 rounded-md bg-surface-container-high px-3 py-1.5 text-label"
            >
              <span className="truncate">MCP · {server}</span>
              <button
                type="button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => onServerUnpin(server)}
                aria-label={`${server} 서버 지목 해제`}
                className="shrink-0 text-on-surface-variant"
              >
                ✕
              </button>
            </span>
          ))}
          {collectionId && (
            <span className="inline-flex max-w-full items-center gap-2 rounded-md bg-surface-container-high px-3 py-1.5 text-label">
              {/* The NAME, and 분류 beside it. A bare name would read as a file
                  the message carries, which is what the chip next to it is. */}
              <span className="truncate">분류 · {currentCollection?.name ?? "삭제된 분류"}</span>
              <button
                type="button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => onCollectionChange(null)}
                aria-label="분류 제한 해제"
                className="shrink-0 text-on-surface-variant"
              >
                ✕
              </button>
            </span>
          )}
          {/* Same row and same shape as an attachment chip: both are "something
              extra this message carries", and the user removes either the same
              way. Its own component would be 30 lines to render two spans. */}
          {toolCall && (
            <span className="inline-flex max-w-full items-center gap-2 rounded-md bg-surface-container-high px-3 py-1.5 text-label">
              <span className="truncate">
                {toolCall.tool.server_name} · {toolCall.tool.name}
              </span>
              {Object.keys(toolCall.arguments).length > 0 && (
                <span className="truncate text-caption text-on-surface-variant">
                  {Object.entries(toolCall.arguments)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(", ")}
                </span>
              )}
              <button
                type="button"
                onClick={onToolRemove}
                aria-label={`${toolCall.tool.name} 도구 제거`}
                className="shrink-0 text-on-surface-variant"
              >
                ✕
              </button>
            </span>
          )}
        </div>
      )}

      {mention && (
        <MentionMenu
          id={mentionId}
          entries={visible}
          activeIndex={Math.min(activeIndex, Math.max(visible.length - 1, 0))}
          query={mention.query}
          onPick={pick}
          onHover={setActiveIndex}
        />
      )}

      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 p-1 pb-2">
          {attachments.map((a) => (
            <AttachmentChip
              key={a.key}
              filename={a.filename}
              sizeBytes={a.sizeBytes}
              kind={a.kind}
              src={a.previewUrl}
              status={a.status === "uploading" ? "uploading" : "ready"}
              error={a.error}
              onRemove={() => onRemove(a.key)}
            />
          ))}
        </div>
      )}

      {/* The settings that survive the send, said out loud where the user is
          still typing. A menu hides state, and "which model is about to answer"
          and "the super agent is on" are answers wanted BEFORE pressing 전송,
          not read off the trace afterwards.
          Above the textarea rather than beside the + button, and that is a
          width decision: at 390px the old row spent 260 of 358 pixels on
          controls, and moving them here is what buys the textarea back. A chip
          row is full-width and squeezes nothing.
          The model chip appears only when there is more than one model - with
          one, "which model" is not a question anyone is asking. */}
      {(models.length > 1 || currentWorkflow || orchestrator) && (
        <div className="flex flex-wrap gap-2 p-1 pb-2">
          {models.length > 1 && currentModel && (
            <StateChip
              icon={CHIP}
              label={currentModel.label}
              onClick={() => setSheet("model")}
              ariaLabel={`답변 모델: ${currentModel.label}`}
            />
          )}
          {currentWorkflow && (
            <StateChip
              icon={SHIELD}
              label={currentWorkflow.name}
              active
              onClick={() => setSheet("workflow")}
              ariaLabel={`워크플로우: ${currentWorkflow.name}`}
              title="누르면 바꿉니다. 이 질문은 이 워크플로우의 절차로 답합니다."
            />
          )}
          {orchestrator && (
            <StateChip
              icon={SPARK}
              label="슈퍼 에이전트"
              active
              onClick={() => onOrchestratorChange(false)}
              ariaLabel="슈퍼 에이전트 켜짐"
              title="누르면 끕니다."
            />
          )}
        </div>
      )}

      <div className="flex items-end gap-2">
        {/* Everything that used to compete along this row lives behind this one
            button now, grouped the way ChatGPT groups its own: what applies to
            THIS message, a divider, then what persists across messages. */}
        <button
          ref={plusRef}
          type="button"
          // Tapping + must not close the keyboard. Reaching for it is the user
          // continuing the same action - they are still composing - and a
          // keyboard that drops costs them the tap to bring it back plus the
          // scroll jump when the viewport resizes twice.
          //
          // A pointer press moves focus by DEFAULT, which blurs the textarea
          // and dismisses the keyboard with it. preventDefault on mousedown
          // suppresses only that focus shift; the click still fires, which is
          // why this is the long-standing pattern for editor toolbar buttons.
          // mousedown rather than pointerdown/touchstart: cancelling a touch
          // sequence that early can also swallow the click on some browsers,
          // and iOS synthesises mousedown before click, so this covers both.
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => setSheet("menu")}
          aria-haspopup="dialog"
          aria-expanded={sheet === "menu"}
          aria-label="추가"
          className="icon-btn"
        >
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          // Cleared after every pick, or choosing the SAME file twice in a row
          // fires no change event and the second attachment never appears.
          onChange={(e) => {
            take(e.target.files);
            e.target.value = "";
            restoreKeyboard();
          }}
        />

        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          onChange={(e) => {
            onChange(e.target.value);
            updateMention(e.target.value, e.target.selectionStart, e.nativeEvent);
          }}
          // Every way the caret can move without the text changing: an arrow, a
          // click, a Home. Without it the menu stays open over a caret that has
          // walked away from the `@` it belongs to.
          onSelect={(e) => {
            const el = e.currentTarget;
            if (mention) updateMention(el.value, el.selectionStart, e.nativeEvent);
          }}
          onCompositionStart={() => {
            composingRef.current = true;
          }}
          onCompositionEnd={(e) => {
            composingRef.current = false;
            // Re-read the token NOW. This is the event that makes `@농약` work:
            // the menu refused to open while 농 was being composed, and this is
            // the moment the syllable becomes real text.
            updateMention(e.currentTarget.value, e.currentTarget.selectionStart, e.nativeEvent);
          }}
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
          onPaste={(e) => {
            // ONLY when the clipboard actually carries a file. Pasting text has
            // to keep working, so an empty `files` list is left entirely alone -
            // no preventDefault, no interception.
            if (e.clipboardData.files.length === 0) return;
            e.preventDefault();
            take(e.clipboardData.files);
          }}
          placeholder="질문을 입력하세요. @로 도구와 워크플로우를 부를 수 있습니다."
          // A placeholder is not an accessible name: it is dropped the moment
          // the field has text, and some screen readers never announce it.
          aria-label="질문"
          // The combobox pattern, and the reason the menu is not a dialog: the
          // textarea keeps focus and OWNS the popup, so a screen reader announces
          // the highlighted row without the caret ever leaving the question.
          role="combobox"
          aria-expanded={mention !== null}
          aria-controls={mention ? mentionId : undefined}
          aria-activedescendant={
            mention && active ? `${mentionId}-${visible.indexOf(active)}` : undefined
          }
          aria-autocomplete="list"
          style={{ maxHeight: MAX_HEIGHT }}
          className="min-w-0 flex-1 resize-none bg-transparent px-2 py-2 text-body-lg text-on-surface placeholder:text-on-surface-variant focus:outline-none"
        />

        {/* The two `key`s are load-bearing, and this was measured. Without them
            React reuses ONE <button> DOM node across the branch and only
            rewrites its `type`. A click's activation behaviour reads `type`
            AFTER the listeners have run, so pressing 중지 went: click ->
            onStop -> abort -> the rejection's setState flushes -> the very same
            node is now type="submit" -> the browser submits the form -> the
            question that 중지 had just restored to the composer was sent
            straight back out. Observed as a second `sending` render with no
            second click, and the 중지 button never going away. Distinct keys
            make React replace the node instead, and a detached button has no
            form owner to submit. */}
        {sending ? (
          // The AbortController ChatWindow already held for unmount, surfaced.
          <button
            key="stop"
            type="button"
            onClick={onStop}
            aria-label="답변 생성 중지"
            className="h-10 shrink-0 rounded-full bg-surface-container-high px-5 text-label font-medium text-on-surface transition-colors duration-150 hover:bg-surface-container-highest"
          >
            중지
          </button>
        ) : (
          // Filled when there is something to send, tonal when there is not -
          // the button says whether the composer is ready without a word.
          <button
            key="send"
            type="submit"
            className={`h-10 shrink-0 rounded-full px-5 text-label font-medium transition-colors duration-150 ${
              value.trim()
                ? "bg-primary text-on-primary"
                : "bg-surface-container-high text-on-surface-variant"
            }`}
          >
            전송
          </button>
        )}
      </div>

      {/* The + menu. Two groups with a rule between them, which is the whole of
          what the owner asked for: 이 메시지에만 is spent on send, 대화 설정
          survives it. role="group" rather than role="menu"/"menuitem" on
          purpose - menu semantics promise arrow-key roving, and this is a
          <dialog> whose keyboard model is Tab, Enter and Escape. Promising the
          arrows and not implementing them is worse than not promising. */}
      <PopoverSheet
        open={sheet === "menu"}
        onClose={closeSheet}
        anchorRef={plusRef}
        label="추가"
      >
        {/* pb-6 is the phone's home indicator. */}
        <div className="p-2 pb-6 sm:pb-2">
          <div role="group" aria-labelledby="composer-menu-message">
            <p
              id="composer-menu-message"
              className="px-3 py-2 text-label font-medium text-on-surface-variant"
            >
              이 메시지에만
            </p>
            <MenuRow
              icon={CLIP}
              label="파일 첨부"
              onClick={() => {
                // Close first, then open the OS file chooser: two overlays over
                // the composer at once is exactly what this menu replaced.
                closeSheet();
                fileRef.current?.click();
              }}
            />
          </div>

          <div
            role="group"
            aria-labelledby="composer-menu-settings"
            className="mt-1 border-t border-outline-variant pt-1"
          >
            <p
              id="composer-menu-settings"
              className="px-3 py-2 text-label font-medium text-on-surface-variant"
            >
              대화 설정
            </p>
            {models.length > 1 && currentModel && (
              <MenuRow
                icon={CHIP}
                label="답변 모델"
                value={currentModel.label}
                onClick={() => setSheet("model")}
              />
            )}
            {workflows.length > 0 && (
              <MenuRow
                icon={SHIELD}
                label="워크플로우"
                value={currentWorkflow?.name ?? DEFAULT_WORKFLOW_LABEL}
                onClick={() => setSheet("workflow")}
              />
            )}
            <MenuRow
              icon={SPARK}
              label="슈퍼 에이전트"
              trailing={<Switch on={orchestrator} />}
              pressed={orchestrator}
              // Stays open: the row IS the state readout, so the user has to be
              // able to see it flip. Closing on toggle would hide the only
              // feedback the action has.
              onClick={() => onOrchestratorChange(!orchestrator)}
              title="질문에 맞춰 모델이 그 자리에서 워크플로우를 짜서 실행합니다."
            />
            {/* 서버 목록은 서브시트로 - 메뉴에 펼치면 서버가 늘어나는 만큼
                메뉴가 자란다(소유자 지적). 값은 켜진 개수 요약. */}
            {mcpServers.length > 0 && (
              <MenuRow
                icon={WRENCH}
                label="도구 설정"
                value={`${mcpServers.filter((s) => s.on).length}/${mcpServers.length} 켬`}
                onClick={() => setSheet("mcp")}
              />
            )}
          </div>
        </div>
      </PopoverSheet>

      <ModelPicker
        models={models}
        value={model}
        onChange={onModelChange}
        open={sheet === "model"}
        onClose={closeSheet}
        anchorRef={plusRef}
        reasoningEffort={reasoningEffort}
        onReasoningEffortChange={onReasoningEffortChange}
        onEffortPicked={() => {
          // 모델 -> 깊이까지 골랐으면 흐름의 끝은 입력창이다(소유자 지시:
          // "설정하고 프롬프트로"). 터치 기기는 예외 - 여기서 포커스를 주면
          // 키보드가 올라온다(전송 후 자판 실사고와 같은 분기).
          setSheet(null);
          if (window.matchMedia("(pointer: coarse)").matches) {
            plusRef.current?.focus();
          } else {
            textareaRef.current?.focus();
          }
        }}
      />

      <WorkflowPicker
        workflows={workflows}
        value={workflowId}
        onChange={onWorkflowChange}
        open={sheet === "workflow"}
        onClose={closeSheet}
        anchorRef={plusRef}
      />

      {/* ToolPicker(도구+JSON 인자 시트)는 화면에서 내려갔다: @도 +도 서버
          단위가 됐고, 인자는 숙고가 뽑거나 되묻는다. 수동 tool_calls 계약은
          백엔드에 그대로 있다 - 워크플로우와 승인 경로가 쓴다. */}

      {/* 도구 설정 - MCP 서버 단위 스위치, 클로드 데스크톱의 커넥터 목록.
          서버가 늘어나면 목록이 스크롤로 감당한다. 끈 서버도 @로 직접 부르는
          길은 항상 열려 있다. */}
      <PopoverSheet
        open={sheet === "mcp"}
        onClose={closeSheet}
        anchorRef={plusRef}
        label="도구 설정"
      >
        <div className="max-h-[60vh] overflow-y-auto p-2 pb-6 sm:pb-2">
          <p className="px-3 py-2 text-label font-medium text-on-surface-variant">
            연결된 MCP 서버
          </p>
          {mcpServers.map((server) => (
            <MenuRow
              key={server.name}
              icon={PLUG}
              label={server.name}
              trailing={<Switch on={server.on} />}
              pressed={server.on}
              // 슈퍼 에이전트와 같은 규칙: 행이 곧 상태 표시라 시트를 닫지
              // 않는다 - 뒤집히는 것이 보여야 한다.
              onClick={() => onMcpServerChange(server.name, !server.on)}
              title="켜 두면 상황이 맞을 때 모델이 이 서버의 도구를 알아서 씁니다. @로 직접 부르는 것은 이 설정과 무관합니다."
            />
          ))}
        </div>
      </PopoverSheet>
    </form>
  );
}
